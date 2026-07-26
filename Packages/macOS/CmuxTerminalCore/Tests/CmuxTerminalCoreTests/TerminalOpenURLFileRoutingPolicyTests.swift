import Foundation
import Testing
import CmuxTerminalCore

@Suite struct TerminalOpenURLFileRoutingPolicyTests {
    private let policy = TerminalOpenURLFileRoutingPolicy()

    @Test func explicitFileSchemeBypassesCmuxFileRouting() throws {
        let url = try #require(URL(string: "file:///Users/dev/out/ab_cosyvoice_emo.wav"))
        #expect(
            policy.shouldAttemptCmuxFileRouting(
                rawOpenURLValue: url.absoluteString,
                target: .external(url)
            ) == false
        )
    }

    @Test func localhostFileSchemeBypassesCmuxFileRouting() throws {
        let url = try #require(URL(string: "file://localhost/Users/dev/out/ab_cosyvoice_emo.wav"))
        #expect(
            policy.shouldAttemptCmuxFileRouting(
                rawOpenURLValue: url.absoluteString,
                target: .external(url)
            ) == false
        )
    }

    @Test func hostedFileTargetBypassesCmuxFileRoutingEvenWithoutRawScheme() throws {
        let url = try #require(URL(string: "file://remote-host/Users/dev/out/ab_cosyvoice_emo.wav"))
        #expect(
            policy.shouldAttemptCmuxFileRouting(
                rawOpenURLValue: "/Users/dev/out/ab_cosyvoice_emo.wav",
                target: .external(url)
            ) == false
        )
    }

    @Test func absolutePathCanStillUseCmuxFileRouting() {
        let url = URL(fileURLWithPath: "/Users/dev/project/README.md")
        #expect(
            policy.shouldAttemptCmuxFileRouting(
                rawOpenURLValue: "/Users/dev/project/README.md",
                target: .external(url)
            )
        )
    }

    @Test func nonFileTargetsBypassCmuxFileRouting() throws {
        let url = try #require(URL(string: "https://example.com/audio.wav"))
        #expect(
            policy.shouldAttemptCmuxFileRouting(
                rawOpenURLValue: url.absoluteString,
                target: .embeddedBrowser(url)
            ) == false
        )
    }

    @Test func unresolvedCodePathsPreventBrowserFallback() {
        #expect(policy.shouldPreventBrowserFallback(rawOpenURLValue: "data/load.py"))
        #expect(policy.shouldPreventBrowserFallback(rawOpenURLValue: "src/main.go:42:7"))
        #expect(policy.shouldPreventBrowserFallback(rawOpenURLValue: "../Sources/App.swift#L12"))
        #expect(policy.shouldPreventBrowserFallback(rawOpenURLValue: "~/project/config.json"))
    }

    @Test func webLinksRemainEligibleForBrowserFallback() {
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "https://example.com/data/load.py"))
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "example.com/docs"))
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "github.com/org/project/data/load.py"))
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "//example.com/assets/app.js"))
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "localhost:3000/app"))
        #expect(!policy.shouldPreventBrowserFallback(rawOpenURLValue: "[::1]:3000/app.py"))
    }
}
