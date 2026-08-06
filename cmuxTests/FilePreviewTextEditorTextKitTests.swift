import AppKit
import Carbon.HIToolbox
import Testing

#if canImport(cmux_DEV)
@testable import cmux_DEV
#elseif canImport(cmux)
@testable import cmux
#endif

/// Regression coverage for the note / file-preview editor TextKit hang (issue #5255,
/// same root-cause family as #4576).
///
/// The freeze is an AppKit modal mouse-tracking loop: `NSTextView.mouseDown` ->
/// `_bellerophonTrackMouseWithMouseDownEvent` -> `NSTextSelectionNavigation`
/// `.textSelectionsInteractingAtPoint` (TextKit 2) -> recursive O(N)
/// `synchronizeTextLayoutManagers`, which pegs the main thread at 100% CPU and freezes
/// the whole app. That TextKit 2 selection path is taken whenever the view has a live
/// `textLayoutManager`.
///
/// The previous mitigation only *read* `.layoutManager`, which merely puts the view in
/// TextKit 2 *compatibility* mode — `textLayoutManager` stays non-nil and the slow
/// selection path remains active (proven by live `sample` captures of the hung process).
/// The structural invariant that actually prevents the hang is that the editor must be a
/// **pure TextKit 1** view, i.e. `textLayoutManager == nil`.
///
/// Note: the existing timing test (`testLargeFileSelectionHitTestStaysResponsive`)
/// exercises `characterIndexForInsertion`, which uses the fast `NSLayoutManager` path
/// even in compatibility mode, so it cannot detect a regression back to the TextKit 2
/// selection path. This invariant test can, with no UI or timing dependency.
@MainActor
@Suite("File preview editor TextKit backing", .serialized)
struct FilePreviewTextEditorTextKitTests {
    @Test("makeFilePreviewTextView is a pure TextKit 1 view (no TextKit 2 selection path)")
    func editorIsPureTextKit1() {
        let textView = SavingTextView.makeFilePreviewTextView()

        // Primary invariant. A TextKit 2 view — or one only dropped to TextKit 2
        // compatibility mode by reading `.layoutManager` — exposes a non-nil
        // `textLayoutManager`, and its selection runs through NSTextSelectionNavigation:
        // the O(N)-per-hit-test main-thread hang. A pure TextKit 1 view has nil here.
        #expect(textView.textLayoutManager == nil)

        // The TextKit 1 stack must be live, with lazy (non-contiguous) glyph layout so
        // multi-hundred-thousand-line documents still open instantly.
        #expect(textView.layoutManager != nil)
        #expect(textView.layoutManager?.allowsNonContiguousLayout == true)
    }

    @Test("line map follows NSString line boundaries and UTF-16 offsets")
    func lineMapUsesTextKitOffsets() {
        let lineMap = FilePreviewLineMap(source: "first\r\nsecond\n😀\n")

        #expect(lineMap.lineStartUTF16Offsets == [0, 7, 14, 17])
        #expect(lineMap.lineCount == 4)
        #expect(lineMap.lineNumber(containingUTF16Offset: 0) == 1)
        #expect(lineMap.lineNumber(containingUTF16Offset: 8) == 2)
        #expect(lineMap.lineNumber(containingUTF16Offset: 15) == 3)
        #expect(lineMap.lineNumber(containingUTF16Offset: 17) == 4)
    }

    @Test("line number ruler keeps the editor on TextKit 1")
    func lineNumberRulerPreservesTextKit1() {
        let scrollView = NSScrollView()
        let textView = SavingTextView.makeFilePreviewTextView()
        scrollView.documentView = textView

        textView.configureLineNumberRuler(in: scrollView, enabled: true)
        #expect(scrollView.hasVerticalRuler)
        #expect(scrollView.verticalRulerView is FilePreviewLineNumberRulerView)
        #expect(textView.textLayoutManager == nil)

        textView.configureLineNumberRuler(in: scrollView, enabled: false)
        #expect(!scrollView.hasVerticalRuler)
        #expect(scrollView.verticalRulerView == nil)
    }

    @Test("line number ruler clips its drawing to its own bounds")
    func lineNumberRulerClipsToBounds() throws {
        let scrollView = NSScrollView()
        let textView = SavingTextView.makeFilePreviewTextView()
        scrollView.documentView = textView

        textView.configureLineNumberRuler(in: scrollView, enabled: true)
        let ruler = try #require(scrollView.verticalRulerView)

        // Since macOS 14, `clipsToBounds` defaults to false and the dirty rect passed to
        // `draw(_:)` can extend beyond the view's bounds. The gutter separator fills the
        // full dirty-rect height, so an unclipped ruler paints its separator upward across
        // the file-path header and its divider.
        #expect(ruler.clipsToBounds)
    }

    /// Every gutter label is positioned from the client's *current* scroll offset, so the
    /// whole gutter goes stale the moment the view scrolls. AppKit only invalidates the
    /// newly exposed strip of a ruler — correct for evenly spaced hash marks, wrong here,
    /// because the retained pixels were drawn for the previous offset. The user-visible
    /// result when scrolling back up is numbers that vanish, and numbers sliced in half at
    /// the strip boundary.
    ///
    /// The invariant that rules that out: what the gutter draws depends only on what is on
    /// screen, never on which sub-rect AppKit asked it to refresh. So a paint of a 20pt
    /// strip must put down exactly the same ink as a paint of the full gutter.
    @Test("line number gutter draws the same labels whatever sub-rect it is asked to refresh")
    func lineNumberGutterDrawingIgnoresDirtyRect() async throws {
        let scrollView = NSScrollView(frame: NSRect(x: 0, y: 0, width: 480, height: 320))
        let textView = SavingTextView.makeFilePreviewTextView()
        textView.string = (1 ... 400).map { "line \($0)" }.joined(separator: "\n")
        scrollView.documentView = textView
        textView.configureLineNumberRuler(in: scrollView, enabled: true)
        scrollView.layoutSubtreeIfNeeded()
        defer { textView.configureLineNumberRuler(in: scrollView, enabled: false) }

        let ruler = try #require(scrollView.verticalRulerView as? FilePreviewLineNumberRulerView)
        try #require(ruler.bounds.width > 0)
        try #require(ruler.bounds.height > 80)

        // `refreshLineNumbers()` indexes the source off the main actor, so the gutter knows
        // about line 1 alone until that lands. Wait for a full-gutter paint to carry more
        // than a single label before asserting anything about partial paints.
        let fullGutterInk = try await ruler.pollForLoadedLineMap()
        #expect(fullGutterInk > 200)

        let topStrip = NSRect(x: 0, y: 0, width: ruler.bounds.width, height: 20)
        #expect(ruler.inkPixelCount(refreshing: topStrip) == fullGutterInk)
    }

    /// A scroll changes every label's position, so the gutter has to repaint in full. AppKit
    /// will not do that on its own — nothing in the ruler observes the clip view — which is
    /// the other half of the stale-pixel bug above.
    @Test("line number gutter invalidates itself when the editor scrolls")
    func lineNumberGutterInvalidatesOnScroll() throws {
        let scrollView = NSScrollView(frame: NSRect(x: 0, y: 0, width: 480, height: 320))
        let textView = SavingTextView.makeFilePreviewTextView()
        textView.string = (1 ... 400).map { "line \($0)" }.joined(separator: "\n")
        scrollView.documentView = textView
        textView.configureLineNumberRuler(in: scrollView, enabled: true)
        scrollView.layoutSubtreeIfNeeded()
        defer { textView.configureLineNumberRuler(in: scrollView, enabled: false) }

        let ruler = try #require(scrollView.verticalRulerView as? FilePreviewLineNumberRulerView)
        let clipView = scrollView.contentView
        clipView.scroll(to: NSPoint(x: 0, y: 600))
        scrollView.reflectScrolledClipView(clipView)
        let generationBeforeSecondScroll = ruler.viewportInvalidationGeneration

        clipView.scroll(to: NSPoint(x: 0, y: 200))
        scrollView.reflectScrolledClipView(clipView)

        #expect(ruler.viewportInvalidationGeneration > generationBeforeSecondScroll)
    }

    @Test("find commands route to the focused text file preview")
    func findCommandsRouteToFocusedTextFilePreview() throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("cmux-file-preview-find-\(UUID().uuidString)")
            .appendingPathExtension("swift")
        try "let value = 42\n".write(to: fileURL, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: fileURL) }

        let manager = TabManager(autoWelcomeIfNeeded: false)
        let workspace = manager.addWorkspace(select: true, eagerLoadTerminal: false)
        defer { workspace.teardownAllPanels() }
        let pane = try #require(workspace.bonsplitController.allPaneIds.first)
        let panel = try #require(workspace.newFilePreviewSurface(
            inPane: pane,
            filePath: fileURL.path,
            focus: true
        ))
        let textView = FindActionRecordingTextView()
        panel.attachTextView(textView)

        #expect(manager.startSearch())
        manager.findNext()
        manager.findPrevious()

        #expect(textView.actions == [
            .showFindInterface,
            .nextMatch,
            .previousMatch,
        ])
    }

    @Test("indent guides identify tabs and complete space indentation levels")
    func indentGuideOffsetsFollowLeadingWhitespace() {
        let spaces = "        value" as NSString
        let tabs = "\t\tvalue" as NSString
        let mixed = "    \tvalue" as NSString
        let partial = "  value" as NSString

        #expect(FilePreviewIndentGuideCalculator.guideUTF16Offsets(
            in: spaces,
            lineRange: NSRange(location: 0, length: spaces.length)
        ) == [0, 4])
        #expect(FilePreviewIndentGuideCalculator.guideUTF16Offsets(
            in: tabs,
            lineRange: NSRange(location: 0, length: tabs.length)
        ) == [0, 1])
        #expect(FilePreviewIndentGuideCalculator.guideUTF16Offsets(
            in: mixed,
            lineRange: NSRange(location: 0, length: mixed.length)
        ) == [0, 4])
        #expect(FilePreviewIndentGuideCalculator.guideUTF16Offsets(
            in: partial,
            lineRange: NSRange(location: 0, length: partial.length)
        ).isEmpty)
    }

    @Test("indent guides preserve whitespace and the pure TextKit 1 editor")
    func indentGuidesPreserveEditorContentAndTextKit1() {
        let textView = SavingTextView.makeFilePreviewTextView()
        let source = "\t\tlet value = 42\n"
        textView.string = source

        textView.configureFilePreviewIndentationGuides(enabled: true, color: .white)

        #expect(textView.string == source)
        #expect(textView.layoutManager is FilePreviewIndentGuideLayoutManager)
        #expect((textView.layoutManager as? FilePreviewIndentGuideLayoutManager)?.showsIndentationGuides == true)
        #expect(textView.textLayoutManager == nil)
    }

    @Test("text preview editor handles standard zoom key equivalents")
    func editorHandlesStandardZoomKeyEquivalents() throws {
        try withDefaultShortcutSettings {
            let textView = SavingTextView.makeFilePreviewTextView()
            let initialPointSize = try #require(textView.font?.pointSize)

            let zoomIn = try #require(Self.keyEvent(characters: "=", keyCode: UInt16(kVK_ANSI_Equal)))
            #expect(textView.performKeyEquivalent(with: zoomIn))
            let zoomedPointSize = try #require(textView.font?.pointSize)
            #expect(zoomedPointSize > initialPointSize)

            let reset = try #require(Self.keyEvent(characters: "0", keyCode: UInt16(kVK_ANSI_0)))
            #expect(textView.performKeyEquivalent(with: reset))
            let resetPointSize = try #require(textView.font?.pointSize)
            #expect(abs(resetPointSize - initialPointSize) < 0.01)

            let zoomOut = try #require(Self.keyEvent(characters: "-", keyCode: UInt16(kVK_ANSI_Minus)))
            #expect(textView.performKeyEquivalent(with: zoomOut))
            let smallerPointSize = try #require(textView.font?.pointSize)
            #expect(smallerPointSize < initialPointSize)
        }
    }

    @Test("text preview zoom in accepts dedicated plus keys")
    func editorZoomInAcceptsDedicatedPlusKeys() throws {
        try withDefaultShortcutSettings {
            let textView = SavingTextView.makeFilePreviewTextView()
            let initialPointSize = try #require(textView.font?.pointSize)
            // kVK_ANSI_RightBracket is the physical key that produces "+" on German/European layouts.
            let event = try #require(Self.keyEvent(characters: "+", keyCode: UInt16(kVK_ANSI_RightBracket)))

            #expect(textView.performKeyEquivalent(with: event))
            let zoomedPointSize = try #require(textView.font?.pointSize)
            #expect(zoomedPointSize > initialPointSize)
        }
    }

    @Test("text preview editor handles chorded zoom key equivalents")
    func editorHandlesChordedZoomKeyEquivalents() throws {
        try withDefaultShortcutSettings {
            KeyboardShortcutSettings.setShortcut(
                StoredShortcut(
                    first: ShortcutStroke(
                        key: "k",
                        command: false,
                        shift: false,
                        option: false,
                        control: true,
                        keyCode: UInt16(kVK_ANSI_K)
                    ),
                    second: ShortcutStroke(
                        key: "=",
                        command: true,
                        shift: false,
                        option: false,
                        control: false,
                        keyCode: UInt16(kVK_ANSI_Equal)
                    )
                ),
                for: .browserZoomIn
            )

            let textView = SavingTextView.makeFilePreviewTextView()
            let initialPointSize = try #require(textView.font?.pointSize)

            let prefix = try #require(Self.keyEvent(
                characters: "k",
                modifierFlags: [.control],
                keyCode: UInt16(kVK_ANSI_K)
            ))
            #expect(textView.performKeyEquivalent(with: prefix))
            #expect(abs((textView.font?.pointSize ?? 0) - initialPointSize) < 0.01)

            let suffix = try #require(Self.keyEvent(characters: "=", keyCode: UInt16(kVK_ANSI_Equal)))
            #expect(textView.performKeyEquivalent(with: suffix))
            let zoomedPointSize = try #require(textView.font?.pointSize)
            #expect(zoomedPointSize > initialPointSize)
        }
    }

    @Test("text preview editor allows save and zoom chords to share a prefix")
    func editorAllowsSaveAndZoomChordsToSharePrefix() throws {
        try withDefaultShortcutSettings {
            KeyboardShortcutSettings.setShortcut(
                Self.controlKChord(secondKey: "s", secondKeyCode: UInt16(kVK_ANSI_S)),
                for: .saveFilePreview
            )
            KeyboardShortcutSettings.setShortcut(
                Self.controlKChord(secondKey: "=", secondKeyCode: UInt16(kVK_ANSI_Equal)),
                for: .browserZoomIn
            )

            let panel = TextEditingPanelSpy()
            let textView = SavingTextView.makeFilePreviewTextView()
            textView.panel = panel
            let initialPointSize = try #require(textView.font?.pointSize)

            let zoomPrefix = try #require(Self.keyEvent(
                characters: "k",
                modifierFlags: [.control],
                keyCode: UInt16(kVK_ANSI_K)
            ))
            #expect(textView.performKeyEquivalent(with: zoomPrefix))

            let zoomSuffix = try #require(Self.keyEvent(characters: "=", keyCode: UInt16(kVK_ANSI_Equal)))
            #expect(textView.performKeyEquivalent(with: zoomSuffix))
            let zoomedPointSize = try #require(textView.font?.pointSize)
            #expect(zoomedPointSize > initialPointSize)
            #expect(panel.saveCount == 0)

            let savePrefix = try #require(Self.keyEvent(
                characters: "k",
                modifierFlags: [.control],
                keyCode: UInt16(kVK_ANSI_K)
            ))
            #expect(textView.performKeyEquivalent(with: savePrefix))

            let saveSuffix = try #require(Self.keyEvent(characters: "s", keyCode: UInt16(kVK_ANSI_S)))
            #expect(textView.performKeyEquivalent(with: saveSuffix))
            #expect(panel.saveCount == 1)
        }
    }

    @Test("text preview editor preserves marked text from printable Option shortcuts")
    func editorPreservesMarkedTextFromPrintableOptionShortcut() throws {
        try withDefaultShortcutSettings {
            KeyboardShortcutSettings.setShortcut(
                StoredShortcut(
                    first: ShortcutStroke(
                        key: "y",
                        command: false,
                        shift: false,
                        option: true,
                        control: false,
                        keyCode: UInt16(kVK_ANSI_Y)
                    )
                ),
                for: .saveFilePreview
            )

            let panel = TextEditingPanelSpy()
            let textView = SavingTextView.makeFilePreviewTextView()
            textView.panel = panel
            textView.setMarkedText(
                "に",
                selectedRange: NSRange(location: 1, length: 0),
                replacementRange: NSRange(location: NSNotFound, length: 0)
            )
            #expect(textView.hasMarkedText())

            let optionSave = try #require(Self.keyEvent(
                characters: "¥",
                charactersIgnoringModifiers: "y",
                modifierFlags: [.option],
                keyCode: UInt16(kVK_ANSI_Y)
            ))
            _ = textView.performKeyEquivalent(with: optionSave)

            #expect(panel.saveCount == 0)
            #expect(textView.hasMarkedText())

            KeyboardShortcutSettings.setShortcut(
                StoredShortcut(
                    first: ShortcutStroke(
                        key: "y",
                        command: true,
                        shift: false,
                        option: false,
                        control: false,
                        keyCode: UInt16(kVK_ANSI_Y)
                    )
                ),
                for: .saveFilePreview
            )
            let commandSave = try #require(Self.keyEvent(
                characters: "y",
                keyCode: UInt16(kVK_ANSI_Y)
            ))

            #expect(textView.performKeyEquivalent(with: commandSave))
            #expect(panel.saveCount == 1)
        }
    }

    @Test("text preview editor clears pending chord shortcuts when leaving a window")
    func editorClearsPendingShortcutChordsWhenLeavingWindow() throws {
        try withDefaultShortcutSettings {
            let originalAppDelegate = AppDelegate.shared
            AppDelegate.shared = nil
            defer { AppDelegate.shared = originalAppDelegate }

            KeyboardShortcutSettings.setShortcut(
                StoredShortcut(
                    first: ShortcutStroke(
                        key: "k",
                        command: false,
                        shift: false,
                        option: false,
                        control: true,
                        keyCode: UInt16(kVK_ANSI_K)
                    ),
                    second: ShortcutStroke(
                        key: "=",
                        command: true,
                        shift: false,
                        option: false,
                        control: false,
                        keyCode: UInt16(kVK_ANSI_Equal)
                    )
                ),
                for: .browserZoomIn
            )

            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 320, height: 240),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false
            )
            let textView = SavingTextView.makeFilePreviewTextView()
            window.contentView = NSView(frame: window.contentRect(forFrameRect: window.frame))
            window.contentView?.addSubview(textView)
            defer { window.orderOut(nil) }

            let initialPointSize = try #require(textView.font?.pointSize)
            let prefix = try #require(Self.keyEvent(
                characters: "k",
                modifierFlags: [.control],
                keyCode: UInt16(kVK_ANSI_K)
            ))
            #expect(textView.performKeyEquivalent(with: prefix))

            textView.removeFromSuperview()

            let suffix = try #require(Self.keyEvent(characters: "=", keyCode: UInt16(kVK_ANSI_Equal)))
            #expect(!textView.performKeyEquivalent(with: suffix))
            #expect(abs((textView.font?.pointSize ?? 0) - initialPointSize) < 0.01)
        }
    }

    @Test("text preview editor honors configured zoom shortcut when clauses")
    func editorHonorsConfiguredZoomShortcutWhenClauses() throws {
        try withShortcutSettingsFile(
            """
            {
              "shortcuts": {
                "when": {
                  "browserZoomIn": "browserFocus"
                }
              }
            }
            """
        ) {
            let textView = SavingTextView.makeFilePreviewTextView()
            let initialPointSize = try #require(textView.font?.pointSize)
            let zoomIn = try #require(Self.keyEvent(characters: "=", keyCode: UInt16(kVK_ANSI_Equal)))

            #expect(!textView.performKeyEquivalent(with: zoomIn))
            #expect(abs((textView.font?.pointSize ?? 0) - initialPointSize) < 0.01)
        }
    }

    private func withDefaultShortcutSettings(_ body: () throws -> Void) rethrows {
        let originalSettingsFileStore = KeyboardShortcutSettings.installIsolatedTestFileStore(
            prefix: "cmux-file-preview-text-zoom"
        )
        KeyboardShortcutSettings.resetAll()
        defer {
            KeyboardShortcutSettings.resetAll()
            KeyboardShortcutSettings.settingsFileStore = originalSettingsFileStore
        }
        try body()
    }

    private func withShortcutSettingsFile(_ contents: String, _ body: () throws -> Void) throws {
        let originalSettingsFileStore = KeyboardShortcutSettings.settingsFileStore
        let settingsFileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("cmux-file-preview-text-zoom-\(UUID().uuidString).json", isDirectory: false)
        try contents.write(to: settingsFileURL, atomically: true, encoding: .utf8)
        KeyboardShortcutSettings.settingsFileStore = KeyboardShortcutSettingsFileStore(
            primaryPath: settingsFileURL.path,
            fallbackPath: nil,
            additionalFallbackPaths: [],
            startWatching: false
        )
        KeyboardShortcutSettings.resetAll()
        defer {
            KeyboardShortcutSettings.resetAll()
            KeyboardShortcutSettings.settingsFileStore = originalSettingsFileStore
            try? FileManager.default.removeItem(at: settingsFileURL)
        }
        try body()
    }

    private static func controlKChord(secondKey: String, secondKeyCode: UInt16) -> StoredShortcut {
        StoredShortcut(
            first: ShortcutStroke(
                key: "k",
                command: false,
                shift: false,
                option: false,
                control: true,
                keyCode: UInt16(kVK_ANSI_K)
            ),
            second: ShortcutStroke(
                key: secondKey,
                command: true,
                shift: false,
                option: false,
                control: false,
                keyCode: secondKeyCode
            )
        )
    }

    private static func keyEvent(
        characters: String,
        charactersIgnoringModifiers: String? = nil,
        modifierFlags: NSEvent.ModifierFlags = [.command],
        keyCode: UInt16
    ) -> NSEvent? {
        NSEvent.keyEvent(
            with: .keyDown,
            location: .zero,
            modifierFlags: modifierFlags,
            timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: 0,
            context: nil,
            characters: characters,
            charactersIgnoringModifiers: charactersIgnoringModifiers ?? characters,
            isARepeat: false,
            keyCode: keyCode
        )
    }

    private final class TextEditingPanelSpy: FilePreviewTextEditingPanel {
        var textContent = ""
        var saveCount = 0

        func attachTextView(_: NSTextView) {}
        func retryPendingFocus() {}
        func updateTextContent(_ nextContent: String) {
            textContent = nextContent
        }

        func saveTextContent() -> Task<Void, Never>? {
            saveCount += 1
            return nil
        }
    }
}

private final class FindActionRecordingTextView: NSTextView {
    private(set) var actions: [NSTextFinder.Action] = []

    override func performFindPanelAction(_ sender: Any?) {
        guard let menuItem = sender as? NSMenuItem,
              let action = NSTextFinder.Action(rawValue: menuItem.tag) else {
            return
        }
        actions.append(action)
    }
}

@MainActor
private extension FilePreviewLineNumberRulerView {
    /// Paints the gutter into an offscreen bitmap, telling it `dirtyRect` needs refreshing,
    /// and returns how many pixels it inked. Comparing two counts is what lets the test talk
    /// about *what was drawn* without depending on where any individual label lands.
    func inkPixelCount(refreshing dirtyRect: NSRect) -> Int {
        guard let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(bounds.width.rounded()),
            pixelsHigh: Int(bounds.height.rounded()),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ), let context = NSGraphicsContext(bitmapImageRep: bitmap) else { return 0 }

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: bounds.size).fill(using: .copy)
        drawHashMarksAndLabels(in: dirtyRect)
        context.flushGraphics()
        NSGraphicsContext.restoreGraphicsState()

        var inked = 0
        for y in 0 ..< bitmap.pixelsHigh {
            for x in 0 ..< bitmap.pixelsWide
                where (bitmap.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.05 {
                inked += 1
            }
        }
        return inked
    }

    /// Waits for the off-main-actor line indexing to land, and returns the ink count of a
    /// full-gutter paint once it has. Detected by that paint carrying more than the single
    /// label an empty line map can produce.
    func pollForLoadedLineMap() async throws -> Int {
        for _ in 0 ..< 200 {
            let inked = inkPixelCount(refreshing: bounds)
            if inked > 200 { return inked }
            try await Task.sleep(for: .milliseconds(10))
        }
        return inkPixelCount(refreshing: bounds)
    }
}
