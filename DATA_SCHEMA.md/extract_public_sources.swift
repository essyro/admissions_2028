import Foundation
import PDFKit
import CryptoKit

struct SourceManifest: Decodable {
    let schemaVersion: Int
    let sourceScope: String
    let documents: [SourceDocument]
}

struct SourceDocument: Decodable {
    let documentVersionId: String
    let fileSha256: String
    let localPath: String
    let filenameNfc: String
}

struct ExtractedPage: Codable, Equatable {
    let pdfPage: Int
    let text: String
    let textStatus: String
}

struct ExtractedDocument: Codable, Equatable {
    let schemaVersion: Int
    let documentVersionId: String
    let fileSha256: String
    let sourceLocalPath: String
    let filenameNfc: String
    let extractionMethod: String
    let reviewStatus: String
    let layoutPreserved: Bool
    let pdfPageCount: Int
    let pages: [ExtractedPage]
}

func require(_ condition: Bool, _ message: String) throws {
    if !condition {
        throw NSError(domain: "PublicSourceExtraction", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}

func run() throws {
    let arguments = CommandLine.arguments
    try require(arguments.count == 3 || arguments.count == 4, "Usage: extract_public_sources MANIFEST OUTPUT_DIRECTORY [DOCUMENT_VERSION_ID]")
    let manifestUrl = URL(fileURLWithPath: arguments[1]).standardizedFileURL
    let projectUrl = manifestUrl.deletingLastPathComponent().deletingLastPathComponent()
    let allowedSourceUrl = projectUrl.appendingPathComponent("1_business_source_data").resolvingSymlinksInPath()
    let outputUrl = URL(fileURLWithPath: arguments[2]).standardizedFileURL
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let manifest = try decoder.decode(SourceManifest.self, from: Data(contentsOf: manifestUrl))
    try require(manifest.schemaVersion == 1 && manifest.sourceScope == "public_university_documents", "Unsupported source manifest")
    let selected = manifest.documents.filter { arguments.count == 3 || $0.documentVersionId == arguments[3] }
    try require(!selected.isEmpty, "No matching public documents")
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    try FileManager.default.createDirectory(at: outputUrl, withIntermediateDirectories: true)
    var totalPages = 0
    var reviewPages = 0

    for source in selected {
        let sourceUrl = projectUrl.appendingPathComponent(source.localPath).standardizedFileURL
        try require(sourceUrl.resolvingSymlinksInPath().deletingLastPathComponent() == allowedSourceUrl, "Source outside allowed public PDF directory")
        let resource = try sourceUrl.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        try require(resource.isRegularFile == true && resource.isSymbolicLink != true, "Source must be a regular file")
        let data = try Data(contentsOf: sourceUrl)
        let actualHash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        try require(actualHash == source.fileSha256 && source.documentVersionId == "sha256:\(actualHash)", "Source hash changed: \(source.filenameNfc)")
        guard let document = PDFDocument(data: data) else {
            throw NSError(domain: "PublicSourceExtraction", code: 2, userInfo: [NSLocalizedDescriptionKey: "Cannot open PDF: \(source.filenameNfc)"])
        }
        try require(!document.isLocked && document.pageCount > 0, "Locked or empty PDF: \(source.filenameNfc)")
        var pages: [ExtractedPage] = []
        for pageIndex in 0..<document.pageCount {
            guard let page = document.page(at: pageIndex) else {
                throw NSError(domain: "PublicSourceExtraction", code: 3, userInfo: [NSLocalizedDescriptionKey: "Missing page \(pageIndex + 1)"])
            }
            let text = page.string ?? ""
            let significantCount = text.filter { !$0.isWhitespace }.count
            let status = significantCount == 0 ? "no_text" : significantCount < 40 ? "sparse_text" : "text_present"
            pages.append(ExtractedPage(pdfPage: pageIndex + 1, text: text, textStatus: status))
        }
        let result = ExtractedDocument(schemaVersion: 1, documentVersionId: source.documentVersionId, fileSha256: actualHash, sourceLocalPath: source.localPath, filenameNfc: source.filenameNfc, extractionMethod: "pdfkit_text", reviewStatus: "unreviewed", layoutPreserved: false, pdfPageCount: document.pageCount, pages: pages)
        let destination = outputUrl.appendingPathComponent(actualHash + ".json")
        if FileManager.default.fileExists(atPath: destination.path) {
            let previous = try decoder.decode(ExtractedDocument.self, from: Data(contentsOf: destination))
            try require(previous == result, "Existing extraction differs; preserve it and use a new output directory")
        } else {
            try encoder.encode(result).write(to: destination, options: .withoutOverwriting)
        }
        let needsReview = pages.filter { $0.textStatus != "text_present" }.count
        totalPages += pages.count
        reviewPages += needsReview
        print("\(source.filenameNfc): pages=\(pages.count), no_or_sparse_text=\(needsReview)")
    }
    print("PASS: documents=\(selected.count), pages=\(totalPages), no_or_sparse_text=\(reviewPages). Content remains unreviewed; no database writes.")
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data(("ERROR: \(error.localizedDescription)\n").utf8))
    exit(1)
}