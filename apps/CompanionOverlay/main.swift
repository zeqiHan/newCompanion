import AppKit
import Foundation
import WebKit

/// Nonactivating floating panel — survives Space switches better than a normal NSWindow.
final class OverlayPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

enum OverlayLog {
    static let dir: URL = {
        let u = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/newCompanion", isDirectory: true)
        try? FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }()

    static func write(_ message: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date()))  \(message)\n"
        let file = dir.appendingPathComponent("overlay.log")
        if let data = line.data(using: .utf8) {
            if FileManager.default.fileExists(atPath: file.path) {
                if let handle = try? FileHandle(forWritingTo: file) {
                    defer { try? handle.close() }
                    _ = try? handle.seekToEnd()
                    try? handle.write(contentsOf: data)
                }
            } else {
                try? data.write(to: file)
            }
        }
        NSLog("%@", message)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    private var window: OverlayPanel?
    private var webView: WKWebView?
    private var statusItem: NSStatusItem?
    private var pipelineProcess: Process?
    private let projectRoot: URL
    private let overlayURL: URL

    override init() {
        self.projectRoot = Self.resolveProjectRoot()
        let envURL = ProcessInfo.processInfo.environment["COMPANION_URL"]
        self.overlayURL = URL(string: envURL ?? "http://127.0.0.1:8765/overlay")!
        super.init()
        OverlayLog.write("projectRoot=\(projectRoot.path)")
        OverlayLog.write("overlayURL=\(overlayURL.absoluteString)")
        OverlayLog.write("bundleURL=\(Bundle.main.bundleURL.path)")
    }

    private static func resolveProjectRoot() -> URL {
        if let marker = Bundle.main.url(forResource: "ProjectRoot", withExtension: "txt"),
           let text = try? String(contentsOf: marker, encoding: .utf8) {
            let path = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let url = URL(fileURLWithPath: path)
            if FileManager.default.fileExists(atPath: url.appendingPathComponent("scripts/start-pipeline.sh").path) {
                return url
            }
        }
        return URL(fileURLWithPath: "/Users/zeq/newCompanion")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        OverlayLog.write("didFinishLaunching")
        ensureWindow()
        installStatusItem(title: "小遥xiaoyao…")
        installSpaceObservers()
        pinOverlay(activate: true)
        webView?.loadHTMLString(
            """
            <html><body style="margin:0;background:transparent;color:#dff;font:600 14px -apple-system;display:grid;place-items:center;height:100vh;">
            正在启动小遥…
            </body></html>
            """,
            baseURL: nil
        )

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            self.ensurePipelineRunning()
            DispatchQueue.main.async {
                self.statusItem?.button?.title = "小遥xiaoyao"
                self.webView?.load(URLRequest(url: self.overlayURL))
                self.pinOverlay(activate: false)
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        OverlayLog.write("dock reopen hasVisibleWindows=\(flag)")
        DispatchQueue.main.async { [weak self] in
            self?.pinOverlay(activate: true)
        }
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        if window != nil { pinOverlay(activate: false) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    private func installSpaceObservers() {
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(spaceOrScreenChanged),
            name: NSWorkspace.activeSpaceDidChangeNotification,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(spaceOrScreenChanged),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )
    }

    @objc private func spaceOrScreenChanged() {
        OverlayLog.write("space/screen changed — follow active space")
        // Wait a tick so Mission Control finishes the Space transition.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) { [weak self] in
            self?.pinOverlay(activate: false, forceMoveToActiveSpace: true)
        }
    }

    private func ensurePipelineRunning() {
        if isPipelineHealthy() {
            OverlayLog.write("pipeline already healthy")
            return
        }
        let script = projectRoot.appendingPathComponent("scripts/start-pipeline.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            OverlayLog.write("MISSING pipeline script at \(script.path)")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", "bash '\(script.path)'"]
        process.currentDirectoryURL = projectRoot
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        process.environment = env
        let logPath = OverlayLog.dir.appendingPathComponent("pipeline.log").path
        FileManager.default.createFile(atPath: logPath, contents: nil)
        if let handle = FileHandle(forWritingAtPath: logPath) {
            process.standardOutput = handle
            process.standardError = handle
        }
        do {
            try process.run()
            pipelineProcess = process
            OverlayLog.write("started pipeline pid=\(process.processIdentifier)")
        } catch {
            OverlayLog.write("failed to start pipeline: \(error)")
            return
        }
        for i in 0..<120 {
            if isPipelineHealthy() {
                OverlayLog.write("pipeline healthy after \(i)s")
                return
            }
            Thread.sleep(forTimeInterval: 1.0)
        }
        OverlayLog.write("pipeline health timeout")
    }

    private func isPipelineHealthy() -> Bool {
        guard let url = URL(string: "http://127.0.0.1:8765/health") else { return false }
        var healthy = false
        let sema = DispatchSemaphore(value: 0)
        let task = URLSession.shared.dataTask(with: url) { data, response, _ in
            defer { sema.signal() }
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let data, let text = String(data: data, encoding: .utf8),
                  text.contains("\"ok\"") else { return }
            healthy = true
        }
        task.resume()
        _ = sema.wait(timeout: .now() + 2)
        return healthy
    }

    private func targetFrame() -> NSRect {
        // Prefer the screen under the mouse after a Space/display switch.
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main
            ?? NSScreen.screens.first
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let width: CGFloat = 360
        let height: CGFloat = 620
        return NSRect(
            x: visible.maxX - width - 18,
            y: visible.minY + 18,
            width: width,
            height: height
        )
    }

    private func applyOverlayBehavior(_ win: OverlayPanel) {
        // moveToActiveSpace: when ordered front, window teleports to the Space you are on.
        // (canJoinAllSpaces is mutually exclusive and was not sticking for this app.)
        win.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        win.level = .floating
        win.isFloatingPanel = true
        win.hidesOnDeactivate = false
        win.becomesKeyOnlyIfNeeded = true
        win.isOpaque = false
        win.backgroundColor = NSColor(calibratedWhite: 0.05, alpha: 0.22)
        win.hasShadow = true
        win.titleVisibility = .visible
        win.titlebarAppearsTransparent = true
        win.isMovableByWindowBackground = true
        win.isReleasedWhenClosed = false
        win.animationBehavior = .none
        win.standardWindowButton(.zoomButton)?.isHidden = true
        win.standardWindowButton(.miniaturizeButton)?.isHidden = true
    }

    private func ensureWindow() {
        if window != nil { return }

        let frame = targetFrame()
        let win = OverlayPanel(
            contentRect: frame,
            styleMask: [.titled, .closable, .fullSizeContentView, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        win.title = "小遥xiaoyao"
        applyOverlayBehavior(win)

        let config = WKWebViewConfiguration()
        config.userContentController.addUserScript(
            WKUserScript(
                source: "var s=document.createElement('style');s.textContent='html,body{background:transparent!important;}';document.documentElement.appendChild(s);",
                injectionTime: .atDocumentEnd,
                forMainFrameOnly: true
            )
        )
        config.userContentController.add(self, name: "overlay")

        let wv = WKWebView(frame: .zero, configuration: config)
        wv.setValue(false, forKey: "drawsBackground")
        wv.wantsLayer = true
        wv.layer?.backgroundColor = NSColor.clear.cgColor
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.autoresizingMask = [.width, .height]

        let content = NSView(frame: NSRect(origin: .zero, size: frame.size))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.clear.cgColor
        wv.frame = content.bounds
        content.addSubview(wv)
        win.contentView = content

        self.window = win
        self.webView = wv
        OverlayLog.write("ensureWindow created (NSPanel)")
    }

    private func installStatusItem(title: String) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = title
        item.button?.toolTip = "newCompanion · 小遥xiaoyao"
        let menu = NSMenu()
        let show = NSMenuItem(title: "显示悬浮窗", action: #selector(showPanel), keyEquivalent: "o")
        let reload = NSMenuItem(title: "重新加载", action: #selector(reloadPage), keyEquivalent: "r")
        let quit = NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q")
        for entry in [show, reload, quit] {
            entry.target = self
            menu.addItem(entry)
        }
        menu.insertItem(NSMenuItem.separator(), at: 2)
        item.menu = menu
        statusItem = item
    }

    @objc private func showPanel() {
        pinOverlay(activate: true, forceMoveToActiveSpace: true)
    }

    private func pinOverlay(activate: Bool, forceMoveToActiveSpace: Bool = false) {
        OverlayLog.write("pinOverlay activate=\(activate) forceMove=\(forceMoveToActiveSpace)")
        ensureWindow()
        guard let win = window else {
            OverlayLog.write("pinOverlay failed")
            return
        }
        if win.isMiniaturized { win.deminiaturize(nil) }

        applyOverlayBehavior(win)
        let frame = targetFrame()

        if forceMoveToActiveSpace {
            // Hide → show makes moveToActiveSpace actually teleport onto the current Space.
            win.orderOut(nil)
            win.setFrame(frame, display: false)
            win.alphaValue = 1.0
            win.setIsVisible(true)
            win.orderFrontRegardless()
        } else {
            win.setFrame(frame, display: true)
            win.alphaValue = 1.0
            win.setIsVisible(true)
            win.orderFrontRegardless()
        }

        if activate {
            win.makeKeyAndOrderFront(nil)
            NSApp.unhide(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        OverlayLog.write("pinOverlay visible=\(win.isVisible) frame=\(NSStringFromRect(win.frame))")
    }

    @objc private func reloadPage() {
        webView?.reload()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "overlay" else { return }
        if let body = message.body as? [String: Any], body["type"] as? String == "quit" {
            quitApp()
        }
    }

    @available(macOS 12.0, *)
    func webView(
        _ webView: WKWebView,
        decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo,
        type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        .grant
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
