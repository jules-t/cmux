import Foundation

@main
enum SyntaxSmoke {
    static func main() {
        let cases: [(String, FilePreviewSyntaxLanguage, String, FilePreviewSyntaxTokenKind)] = [
            ("let value = 42 // Swift", .swift, "let", .keyword),
            ("def value():\n    return 42", .python, "def", .keyword),
            ("package main\nfunc main() {}", .go, "func", .keyword),
            ("fn value<'a>(x: &'a str) {}", .rust, "fn", .keyword),
            ("const value: number = 42;", .typescript, "const", .keyword),
        ]
        for (source, language, expectedText, expectedKind) in cases {
            let tokens = FilePreviewSyntaxTokenizer.tokens(in: source, language: language)
            let found = tokens.contains { token in
                token.kind == expectedKind
                    && (source as NSString).substring(with: token.range) == expectedText
            }
            precondition(found, "missing \(expectedKind) token \(expectedText) for \(language)")
        }

        let emoji = "let launch = \"🚀\" // done"
        let length = (emoji as NSString).length
        for token in FilePreviewSyntaxTokenizer.tokens(in: emoji, language: .swift) {
            precondition(token.range.location >= 0)
            precondition(token.range.location + token.range.length <= length)
        }

        precondition(
            FilePreviewSyntaxLanguage.detect(for: URL(fileURLWithPath: "/tmp/main.py")) == .python
        )
        precondition(
            FilePreviewSyntaxLanguage.detect(for: URL(fileURLWithPath: "/tmp/main.go")) == .go
        )
        print("syntax smoke: ok")
    }
}
