public import Foundation

/// Decides whether a terminal open-URL target may be handled by cmux's file UI.
public struct TerminalOpenURLFileRoutingPolicy: Sendable {
    private static let localFileExtensions: Set<String> = [
        "bash", "c", "cc", "cfg", "conf", "cpp", "cs", "css", "cts", "fish",
        "go", "h", "hh", "hpp", "hxx", "ini", "java", "js", "json", "jsonc",
        "jsx", "kt", "kts", "less", "m", "markdown", "md", "mdx", "mjs", "mm",
        "mts", "php", "py", "pyi", "rb", "rs", "scss", "sh", "sql", "swift",
        "toml", "ts", "tsx", "txt", "xml", "yaml", "yml", "zsh"
    ]

    /// Creates the file-routing policy.
    public init() {}

    /// Returns whether cmux may attempt to open the target in its file preview UI.
    ///
    /// The caller still owns settings, file existence, workspace locality, and
    /// split creation checks. This policy only answers whether the raw terminal
    /// open-URL payload represents a local file shape that cmux is allowed to
    /// intercept before the normal URL routing decision.
    ///
    /// - Parameters:
    ///   - rawOpenURLValue: The raw open-URL payload from the terminal runtime.
    ///   - target: The parsed terminal link target.
    public func shouldAttemptCmuxFileRouting(
        rawOpenURLValue: String,
        target: TerminalOpenURLTarget
    ) -> Bool {
        guard !hasExplicitURLScheme(rawOpenURLValue) else { return false }
        guard target.url.isFileURL else { return false }
        return isLocalFileURL(target.url)
    }

    /// Returns true when unresolved scheme-less text is shaped like a local
    /// code/text path and must not be reinterpreted as an HTTPS host.
    ///
    /// Existing files are resolved before this policy is consulted. This
    /// fallback only prevents values such as `data/load.py` from becoming
    /// `https://data/load.py` when the path is stale or malformed.
    public func shouldPreventBrowserFallback(rawOpenURLValue: String) -> Bool {
        let trimmed = rawOpenURLValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !hasExplicitURLScheme(trimmed) else { return false }

        if trimmed.hasPrefix("//") {
            return false
        }
        if trimmed.hasPrefix("/") ||
            trimmed.hasPrefix("./") ||
            trimmed.hasPrefix("../") ||
            trimmed.hasPrefix("~/") {
            return true
        }

        guard trimmed.contains("/") else { return false }
        if let firstComponent = trimmed.split(separator: "/", maxSplits: 1).first {
            let hostCandidate = firstComponent.lowercased()
            if hostCandidate == "localhost" ||
                hostCandidate.contains(".") ||
                (hostCandidate.hasPrefix("[") && hostCandidate.contains("]")) {
                return false
            }
        }

        for candidate in trimmed.pathResolutionCandidates() {
            let ext = (candidate as NSString).pathExtension.lowercased()
            if Self.localFileExtensions.contains(ext) {
                return true
            }
        }
        return false
    }

    private func hasExplicitURLScheme(_ rawValue: String) -> Bool {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let scheme = URL(string: trimmed)?.scheme else { return false }
        return !scheme.isEmpty
    }

    private func isLocalFileURL(_ url: URL) -> Bool {
        url.host?.isEmpty ?? true
    }
}
