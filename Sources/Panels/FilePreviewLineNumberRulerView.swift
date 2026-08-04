import AppKit

@MainActor
final class FilePreviewLineNumberRulerView: NSRulerView {
    private static let minimumThickness: CGFloat = 42
    private static let horizontalPadding: CGFloat = 16

    private var lineMap = FilePreviewLineMap(source: "")
    private var numberColor = NSColor.secondaryLabelColor
    private var separatorColor = NSColor.separatorColor
    private var refreshGeneration = 0
    private var pendingRefreshTask: Task<Void, Never>?

    override var isFlipped: Bool {
        true
    }

    init(scrollView: NSScrollView, textView: SavingTextView) {
        super.init(scrollView: scrollView, orientation: .verticalRuler)
        clientView = textView
        wantsLayer = true
        // macOS 14+ defaults clipsToBounds to false and passes draw(_:) dirty rects that
        // extend beyond the view bounds, letting the separator paint over the file-path
        // header above the scroll view.
        clipsToBounds = true
        ruleThickness = Self.minimumThickness
        refreshLineNumbers()
    }

    @available(*, unavailable)
    required init(coder _: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    deinit {
        pendingRefreshTask?.cancel()
    }

    func configureAppearance(
        backgroundColor: NSColor,
        foregroundColor: NSColor,
        drawsBackground: Bool
    ) {
        layer?.backgroundColor = (drawsBackground ? backgroundColor : .clear).cgColor
        numberColor = foregroundColor.withAlphaComponent(0.52)
        separatorColor = foregroundColor.withAlphaComponent(0.12)
        needsDisplay = true
    }

    func refreshLineNumbers() {
        guard let textView = clientView as? SavingTextView else { return }
        pendingRefreshTask?.cancel()
        refreshGeneration &+= 1
        let generation = refreshGeneration
        let source = textView.string
        let indexingTask = Task.detached(priority: .userInitiated) {
            await FilePreviewLineMap.build(source: source)
        }

        pendingRefreshTask = Task { [weak self] in
            let nextMap = await withTaskCancellationHandler {
                await indexingTask.value
            } onCancel: {
                indexingTask.cancel()
            }
            guard !Task.isCancelled,
                  let self,
                  self.refreshGeneration == generation,
                  let nextMap else { return }
            self.lineMap = nextMap
            self.refreshMetrics()
        }
    }

    func refreshMetrics() {
        let font = labelFont
        let digitCount = max(2, String(max(1, lineMap.lineCount)).count)
        let labelWidth = (String(repeating: "8", count: digitCount) as NSString)
            .size(withAttributes: [.font: font])
            .width
        let nextThickness = max(Self.minimumThickness, ceil(labelWidth + Self.horizontalPadding))
        if abs(ruleThickness - nextThickness) > 0.5 {
            ruleThickness = nextThickness
            scrollView?.tile()
        }
        needsDisplay = true
    }

    func cancelRefresh() {
        pendingRefreshTask?.cancel()
        pendingRefreshTask = nil
        refreshGeneration &+= 1
    }

    override func drawHashMarksAndLabels(in rect: NSRect) {
        guard let textView = clientView as? SavingTextView,
              let layoutManager = textView.layoutManager,
              let textContainer = textView.textContainer else { return }

        drawSeparator(in: rect)

        let visibleRect = textView.visibleRect
        let textContainerOrigin = textView.textContainerOrigin
        let visibleContainerRect = visibleRect.offsetBy(
            dx: -textContainerOrigin.x,
            dy: -textContainerOrigin.y
        )
        let glyphRange = layoutManager.glyphRange(
            forBoundingRect: visibleContainerRect,
            in: textContainer
        )
        let characterRange = layoutManager.characterRange(
            forGlyphRange: glyphRange,
            actualGlyphRange: nil
        )
        let attributes = labelAttributes
        let labelHeight = labelFont.ascender - labelFont.descender + labelFont.leading

        for lineIndex in lineMap.lineIndices(intersectingUTF16Range: characterRange) {
            let characterOffset = lineMap.lineStartUTF16Offsets[lineIndex]
            guard let fragmentRect = lineFragmentRect(
                atUTF16Offset: characterOffset,
                textLength: textView.textStorage?.length ?? 0,
                layoutManager: layoutManager
            ) else { continue }

            let y = textContainerOrigin.y + fragmentRect.minY - visibleRect.minY
            let labelRect = NSRect(
                x: 4,
                y: floor(y + (fragmentRect.height - labelHeight) / 2),
                width: max(0, ruleThickness - 12),
                height: ceil(labelHeight)
            )
            guard labelRect.intersects(rect) else { continue }
            (String(lineIndex + 1) as NSString).draw(in: labelRect, withAttributes: attributes)
        }
    }

    private var labelFont: NSFont {
        let editorPointSize = (clientView as? NSTextView)?.font?.pointSize ?? 13
        return NSFont.monospacedDigitSystemFont(
            ofSize: max(9, editorPointSize * 0.84),
            weight: .regular
        )
    }

    private var labelAttributes: [NSAttributedString.Key: Any] {
        let paragraphStyle = NSMutableParagraphStyle()
        paragraphStyle.alignment = .right
        return [
            .font: labelFont,
            .foregroundColor: numberColor,
            .paragraphStyle: paragraphStyle,
        ]
    }

    private func lineFragmentRect(
        atUTF16Offset offset: Int,
        textLength: Int,
        layoutManager: NSLayoutManager
    ) -> NSRect? {
        if offset < textLength {
            let glyphIndex = layoutManager.glyphIndexForCharacter(at: offset)
            return layoutManager.lineFragmentRect(forGlyphAt: glyphIndex, effectiveRange: nil)
        }

        let extraFragmentRect = layoutManager.extraLineFragmentRect
        return extraFragmentRect.isEmpty ? nil : extraFragmentRect
    }

    private func drawSeparator(in rect: NSRect) {
        separatorColor.setFill()
        NSRect(
            x: ruleThickness - 1,
            y: rect.minY,
            width: 1,
            height: rect.height
        ).fill()
    }
}

@MainActor
extension SavingTextView {
    func configureLineNumberRuler(in scrollView: NSScrollView, enabled: Bool) {
        if enabled {
            if let ruler = lineNumberRulerView {
                ruler.refreshMetrics()
                ruler.needsDisplay = true
                return
            }

            let ruler = FilePreviewLineNumberRulerView(scrollView: scrollView, textView: self)
            lineNumberRulerView = ruler
            scrollView.verticalRulerView = ruler
            scrollView.hasVerticalRuler = true
            scrollView.rulersVisible = true
            return
        }

        lineNumberRulerView?.cancelRefresh()
        lineNumberRulerView = nil
        scrollView.verticalRulerView = nil
        scrollView.hasVerticalRuler = false
        scrollView.rulersVisible = false
    }
}
