#!/usr/bin/env swift
//
// transcribe-apple.swift — 用 Apple 的 SpeechAnalyzer (macOS 26+) 轉錄音檔
//
// 用法:
//   swift scripts/transcribe-apple.swift _inbox/2026-04-08.m4a
//   swift scripts/transcribe-apple.swift _inbox/2026-04-08.m4a --lang zh-TW
//
// 輸出: _transcripts_raw/<檔名>_apple.txt
//
// 跟舊版的差異:
// - 改用 SpeechAnalyzer + SpeechTranscriber,
//   就是 iOS 26 / macOS 26 語音備忘錄背後的同一個本地引擎
// - 純本地、沒有雲端逾時、沒有 ~50 分鐘硬上限
// - 第一次跑某個 locale 會自動下載對應模型(zh-TW 約幾百 MB)
// - 不再需要 SFSpeechRecognizer 的「語音辨識」權限,因為它根本不送到 Apple 伺服器
//
// 需要 macOS 26.0+,Swift 6+。

import Foundation
import AVFoundation
import Speech
import Darwin

// 強制 stdout 行緩衝(non-TTY 預設是全緩衝,訊息會卡在 buffer 裡)
setbuf(stdout, nil)

// === 解析參數 ===
let args = CommandLine.arguments
guard args.count >= 2 else {
    print("用法: swift transcribe-apple.swift <音檔路徑> [--lang zh-TW]")
    exit(1)
}

let inputPath = args[1]
var language = "zh-TW"
if let langIdx = args.firstIndex(of: "--lang"), langIdx + 1 < args.count {
    language = args[langIdx + 1]
}

let inputURL = URL(fileURLWithPath: inputPath)
guard FileManager.default.fileExists(atPath: inputURL.path) else {
    print("✗ 找不到檔案: \(inputPath)")
    exit(1)
}

// 推算輸出路徑(腳本位於 scripts/,專案根目錄是上一層)
let scriptURL = URL(fileURLWithPath: args[0])
let projectRoot = scriptURL.deletingLastPathComponent().deletingLastPathComponent()
let rawDir = projectRoot.appendingPathComponent("_transcripts_raw")
try? FileManager.default.createDirectory(at: rawDir, withIntermediateDirectories: true)

let stem = inputURL.deletingPathExtension().lastPathComponent
let outputURL = rawDir.appendingPathComponent("\(stem)_apple.txt")

print("→ 輸入: \(inputURL.path)")
print("→ 輸出: \(outputURL.path)")
print("→ 語言: \(language)")
print("→ 引擎: SpeechAnalyzer (本地,iOS/macOS 26+ 同一個引擎)")

guard #available(macOS 26.0, *) else {
    print("✗ SpeechAnalyzer 需要 macOS 26.0 以上。你這台太舊了。")
    exit(1)
}

// === 用 semaphore 把 async 包成 script-mode 可跑的同步流程 ===
let semaphore = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0

if #available(macOS 26.0, *) {
    Task {
        do {
            let locale = Locale(identifier: language)

            // 確認 locale 受支援
            let supportedLocales = await SpeechTranscriber.supportedLocales
            guard supportedLocales.contains(where: { $0.identifier(.bcp47) == locale.identifier(.bcp47) }) else {
                print("✗ Locale \(language) 不被 SpeechTranscriber 支援")
                exitCode = 1
                semaphore.signal()
                return
            }

            // 釋放舊的 reserved locales,然後 reserve 這次要用的
            for reservedLocale in await AssetInventory.reservedLocales {
                await AssetInventory.release(reservedLocale: reservedLocale)
            }
            try await AssetInventory.reserve(locale: locale)

            let transcriber = SpeechTranscriber(
                locale: locale,
                transcriptionOptions: [],
                reportingOptions: [],
                attributeOptions: []
            )
            let modules: [any SpeechModule] = [transcriber]

            // === 確保模型已下載 ===
            let installed = await SpeechTranscriber.installedLocales
            if !installed.contains(where: { $0.identifier(.bcp47) == locale.identifier(.bcp47) }) {
                print("→ 第一次使用 \(language),下載模型中(zh-TW 約幾百 MB,只下載一次)…")
                if let request = try await AssetInventory.assetInstallationRequest(supporting: modules) {
                    try await request.downloadAndInstall()
                    print("→ 模型下載完成")
                } else {
                    print("⚠ 沒有可下載的模型,可能此 locale 暫時不可下載")
                }
            }

            print("→ 辨識中…")
            let startedAt = Date()

            let analyzer = SpeechAnalyzer(modules: modules)
            let audioFile = try AVAudioFile(forReading: inputURL)
            try await analyzer.start(inputAudioFile: audioFile, finishAfterFile: true)

            // 收集結果
            var transcript: AttributedString = ""
            for try await result in transcriber.results {
                transcript += result.text
            }

            let plainText = String(transcript.characters)
                .trimmingCharacters(in: .whitespacesAndNewlines)

            try plainText.write(to: outputURL, atomically: true, encoding: .utf8)

            let elapsed = Int(Date().timeIntervalSince(startedAt))
            print("✓ 完成: \(outputURL.path)")
            print("✓ 字數: \(plainText.count),耗時 \(elapsed) 秒")
        } catch {
            let nsError = error as NSError
            print("✗ 辨識失敗: \(error.localizedDescription)")
            print("   錯誤碼: \(nsError.code), domain: \(nsError.domain)")
            exitCode = 1
        }
        semaphore.signal()
    }
}

semaphore.wait()
exit(exitCode)
