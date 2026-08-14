import Foundation
import Speech

@main
struct MacSTT {
    static func main() {
        let args = CommandLine.arguments
        // mac_stt <audio> [locale] [out.txt]
        guard args.count >= 2 else {
            fputs("usage: mac_stt <audio-file> [locale] [out-file]\n", stderr)
            exit(2)
        }
        let path = (args[1] as NSString).expandingTildeInPath
        let localeId = args.count >= 3 ? args[2] : "zh-CN"
        let outPath = args.count >= 4 ? (args[3] as NSString).expandingTildeInPath : nil

        guard FileManager.default.fileExists(atPath: path) else {
            fputs("file not found: \(path)\n", stderr)
            writeOut(outPath, text: "", error: "file not found: \(path)")
            exit(3)
        }

        let url = URL(fileURLWithPath: path)

        // Speech callbacks often land on the main queue. Never block main with
        // a plain semaphore wait — pump the run loop instead.
        var authStatus = SFSpeechRecognizer.authorizationStatus()
        if authStatus == .notDetermined {
            var decided = false
            SFSpeechRecognizer.requestAuthorization { status in
                authStatus = status
                decided = true
            }
            if !waitUntil({ decided }, timeout: 90) {
                let msg = "authorization dialog timed out — enable MacSTT in System Settings → Privacy & Security → Speech Recognition"
                fputs(msg + "\n", stderr)
                writeOut(outPath, text: "", error: msg)
                exit(4)
            }
        }

        guard authStatus == .authorized else {
            let msg = "speech recognition not authorized (\(authStatus.rawValue)). Enable MacSTT in System Settings → Privacy & Security → Speech Recognition."
            fputs(msg + "\n", stderr)
            writeOut(outPath, text: "", error: msg)
            exit(4)
        }

        let localeCandidates = [localeId, "zh-CN", "zh_CN", "zh-Hans", "en-US"]
        var recognizer: SFSpeechRecognizer?
        var usedLocale = localeId
        for id in localeCandidates {
            if let r = SFSpeechRecognizer(locale: Locale(identifier: id)), r.isAvailable {
                recognizer = r
                usedLocale = id
                break
            }
        }
        guard let recognizer else {
            let msg = "recognizer unavailable for locale \(localeId)"
            fputs(msg + "\n", stderr)
            writeOut(outPath, text: "", error: msg)
            exit(5)
        }

        // Try on-device first when supported; fall back to Apple servers.
        let preferOnDevice = recognizer.supportsOnDeviceRecognition
        if let text = recognize(
            url: url,
            recognizer: recognizer,
            onDevice: preferOnDevice,
            timeout: 45
        ), !text.isEmpty {
            print(text)
            writeOut(outPath, text: text, error: nil)
            exit(0)
        }

        if preferOnDevice {
            fputs("on-device failed/empty; retrying with network recognition…\n", stderr)
            if let text = recognize(
                url: url,
                recognizer: recognizer,
                onDevice: false,
                timeout: 45
            ), !text.isEmpty {
                print(text)
                writeOut(outPath, text: text, error: nil)
                exit(0)
            }
        }

        let msg = "recognize timeout/empty (locale=\(usedLocale))"
        fputs(msg + "\n", stderr)
        writeOut(outPath, text: "", error: msg)
        exit(7)
    }

    static func recognize(
        url: URL,
        recognizer: SFSpeechRecognizer,
        onDevice: Bool,
        timeout: TimeInterval
    ) -> String? {
        let request = SFSpeechURLRecognitionRequest(url: url)
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        if onDevice {
            request.requiresOnDeviceRecognition = true
        }

        var finalText = ""
        var bestPartial = ""
        var errMsg: String?
        var finished = false

        let task = recognizer.recognitionTask(with: request) { result, error in
            if let error = error {
                errMsg = error.localizedDescription
                finished = true
                return
            }
            guard let result = result else { return }
            let text = result.bestTranscription.formattedString
            if !text.isEmpty {
                bestPartial = text
            }
            if result.isFinal {
                finalText = text
                finished = true
            }
        }

        let ok = waitUntil({ finished }, timeout: timeout)
        task.cancel()

        if let errMsg, finalText.isEmpty && bestPartial.isEmpty {
            fputs("recognize error: \(errMsg)\n", stderr)
            return nil
        }
        let text = finalText.isEmpty ? bestPartial : finalText
        if !ok && text.isEmpty {
            return nil
        }
        return text
    }

    /// Wait while pumping the main run loop so Speech callbacks can fire.
    static func waitUntil(_ predicate: () -> Bool, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !predicate() {
            if Date() >= deadline {
                return false
            }
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
        return true
    }

    static func writeOut(_ path: String?, text: String, error: String?) {
        guard let path else { return }
        if let error, text.isEmpty {
            try? error.write(toFile: path + ".err", atomically: true, encoding: .utf8)
            try? "".write(toFile: path, atomically: true, encoding: .utf8)
            return
        }
        try? text.write(toFile: path, atomically: true, encoding: .utf8)
        try? FileManager.default.removeItem(atPath: path + ".err")
    }
}
