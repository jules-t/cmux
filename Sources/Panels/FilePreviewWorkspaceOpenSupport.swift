import Bonsplit
import Foundation

enum FileSurfaceSplitPresentation {
    case automatic
    case markdown
    case filePreview
}

extension Workspace {
    /// Open a file beside `sourcePanelId`, reusing the existing right-side
    /// pane when possible. This is the shared placement path for terminal
    /// command-clicks and `cmux open`.
    @discardableResult
    func openOrFocusFileSplit(
        from sourcePanelId: UUID,
        filePath: String,
        presentation: FileSurfaceSplitPresentation = .automatic,
        focus: Bool = true
    ) -> (any Panel)? {
        let useMarkdownViewer: Bool
        switch presentation {
        case .automatic:
            useMarkdownViewer = MarkdownPanelFileLinkResolver.isMarkdownPathLike(filePath)
        case .markdown:
            useMarkdownViewer = true
        case .filePreview:
            useMarkdownViewer = false
        }

        if useMarkdownViewer {
            return openOrFocusMarkdownSplit(
                from: sourcePanelId,
                filePath: filePath,
                focus: focus
            )
        }
        return openOrFocusFilePreviewSplit(
            from: sourcePanelId,
            filePath: filePath,
            focus: focus
        )
    }

    /// Open the first file in a right-side split and any remaining files as
    /// tabs in that same viewer pane.
    @discardableResult
    func openFileSurfacesBeside(
        sourcePanelId: UUID,
        filePaths: [String],
        focus: Bool
    ) -> [any Panel] {
        guard let firstPath = filePaths.first,
              let firstPanel = openOrFocusFileSplit(
                  from: sourcePanelId,
                  filePath: firstPath,
                  focus: focus
              ),
              let targetPane = paneId(forPanelId: firstPanel.id) else {
            return []
        }

        let remainingPanels = openFileSurfaces(
            inPane: targetPane,
            filePaths: Array(filePaths.dropFirst()),
            focus: focus
        )
        return [firstPanel] + remainingPanels
    }

    @discardableResult
    func openFileSurfaces(
        inPane paneId: PaneID,
        filePaths: [String],
        focus: Bool? = nil,
        targetIndex: Int? = nil,
        reuseExisting: Bool = false
    ) -> [any Panel] {
        let shouldFocusNewTabs = focus ?? (bonsplitController.focusedPaneId == paneId)
        var nextIndex = targetIndex
        var openedPanels: [any Panel] = []

        for filePath in filePaths {
            let panel: (any Panel)?
            let pathExtension = (filePath as NSString).pathExtension.lowercased()
            if pathExtension == "xcodeproj" || pathExtension == "xcworkspace" {
                panel = newProjectSurface(
                    inPane: paneId,
                    projectPath: filePath,
                    focus: shouldFocusNewTabs,
                    targetIndex: nextIndex
                )
            } else if MarkdownPanelFileLinkResolver.isMarkdownPathLike(filePath) {
                if reuseExisting {
                    panel = openOrFocusMarkdownSurface(
                        inPane: paneId,
                        filePath: filePath,
                        focus: shouldFocusNewTabs
                    )
                } else {
                    panel = newMarkdownSurface(
                        inPane: paneId,
                        filePath: filePath,
                        focus: shouldFocusNewTabs,
                        targetIndex: nextIndex
                    )
                }
            } else if reuseExisting {
                panel = openOrFocusFilePreviewSurface(
                    inPane: paneId,
                    filePath: filePath,
                    focus: shouldFocusNewTabs
                )
            } else {
                panel = newFilePreviewSurface(
                    inPane: paneId,
                    filePath: filePath,
                    focus: shouldFocusNewTabs,
                    targetIndex: nextIndex
                )
            }

            if let panel {
                openedPanels.append(panel)
                if let index = nextIndex {
                    nextIndex = index + 1
                }
            }
        }

        return openedPanels
    }

    @discardableResult
    func openFilePreviewSurfaces(
        inPane paneId: PaneID,
        filePaths: [String],
        focus: Bool? = nil,
        targetIndex: Int? = nil,
        reuseExisting: Bool = false
    ) -> [FilePreviewPanel] {
        let shouldFocusNewTabs = focus ?? (bonsplitController.focusedPaneId == paneId)
        var nextIndex = targetIndex
        var openedPanels: [FilePreviewPanel] = []

        for filePath in filePaths {
            let panel: FilePreviewPanel?
            if reuseExisting {
                panel = openOrFocusFilePreviewSurface(
                    inPane: paneId,
                    filePath: filePath,
                    focus: shouldFocusNewTabs
                )
            } else {
                panel = newFilePreviewSurface(
                    inPane: paneId,
                    filePath: filePath,
                    focus: shouldFocusNewTabs,
                    targetIndex: nextIndex
                )
            }

            if let panel {
                openedPanels.append(panel)
                if let index = nextIndex {
                    nextIndex = index + 1
                }
            }
        }

        return openedPanels
    }
}
