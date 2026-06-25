import Cocoa
import Foundation

private let tokenMeterMenubarURL = URL(string: "http://127.0.0.1:8722/menubar")!
private let tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/")!

enum Verdict {
    case healthy
    case watch
    case intervene
    case idle
    case disconnected

    var label: String {
        switch self {
        case .healthy: return "Healthy"
        case .watch: return "Watch closely"
        case .intervene: return "Intervene now"
        case .idle: return "Idle"
        case .disconnected: return "Server offline"
        }
    }

    var prefix: String {
        switch self {
        case .healthy: return "TM"
        case .watch: return "TM !"
        case .intervene: return "TM !!"
        case .idle: return "TM idle"
        case .disconnected: return "TM off"
        }
    }

    var color: NSColor {
        switch self {
        case .healthy: return .labelColor
        case .watch: return .systemOrange
        case .intervene: return .systemRed
        case .idle: return .secondaryLabelColor
        case .disconnected: return .secondaryLabelColor
        }
    }
}

struct MeterSnapshot {
    var connected: Bool
    var error: String
    var verdict: Verdict
    var provider: String
    var project: String
    var session: String
    var pricingNote: String
    var totalCost: Double
    var estimatedCost: Bool
    var totalTokens: Int
    var turns: Int
    var contextPct: Double?
    var contextTokens: Int
    var contextWindow: Int
    var lastTurnCost: Double
    var idleSeconds: Int
    var ended: Bool
    var activityKind: String
    var activityTitle: String
    var activityDetail: String
    var activityTime: String
    var recommendationLabel: String
    var recommendationDetail: String
    var recommendationSeverity: String
    var topSignal: String

    static func disconnected(_ error: String) -> MeterSnapshot {
        MeterSnapshot(
            connected: false,
            error: error,
            verdict: .disconnected,
            provider: "Token Meter",
            project: "",
            session: "",
            pricingNote: "",
            totalCost: 0,
            estimatedCost: false,
            totalTokens: 0,
            turns: 0,
            contextPct: nil,
            contextTokens: 0,
            contextWindow: 0,
            lastTurnCost: 0,
            idleSeconds: 0,
            ended: false,
            activityKind: "offline",
            activityTitle: "Server offline",
            activityDetail: error,
            activityTime: "",
            recommendationLabel: "Start server",
            recommendationDetail: "Run Token Meter to load live session state.",
            recommendationSeverity: "idle",
            topSignal: error
        )
    }

    static func fromJSON(_ dict: [String: Any]) -> MeterSnapshot {
        let source = dict["source"] as? [String: Any] ?? [:]
        let context = dict["context"] as? [String: Any] ?? [:]
        let insights = dict["insights"] as? [[String: Any]] ?? []

        let provider = string(source["label"]) ?? string(dict["provider"]) ?? "Token Meter"
        let project = string(source["project"]) ?? string(dict["project"]) ?? ""
        let session = string(dict["session"]) ?? string(source["id"]) ?? ""
        let pricingNote = string(source["pricing_note"]) ?? ""
        let totalCost = double(dict["total_cost"])
        let estimatedCost = bool(dict["cost_approx"]) || bool(source["approximate_cost"])
        let totalTokens = int(dict["total_tokens"])
        let turns = int(dict["turns"])
        let contextPct = optionalDouble(context["latest_pct"])
        let contextTokens = int(context["latest"])
        let contextWindow = int(context["window"])
        let lastTurnCost = double(dict["last_turn_cost"])
        let idleSeconds = int(dict["idle_s"])
        let ended = bool(dict["ended"])
        let activity = dict["activity"] as? [String: Any] ?? [:]
        let activityKind = string(activity["kind"]) ?? "activity"
        let activityTitle = string(activity["title"]) ?? "Waiting for activity"
        let activityDetail = string(activity["detail"]) ?? ""
        let activityTime = string(activity["time"]) ?? ""
        let recommendation = dict["recommendation"] as? [String: Any] ?? [:]
        let recommendationLabel = string(recommendation["label"]) ?? "Let it run"
        let recommendationDetail = string(recommendation["detail"]) ?? "No immediate intervention needed."
        let recommendationSeverity = string(recommendation["severity"]) ?? "good"

        let warn = insights.first { string($0["kind"]) == "warn" }
        let firstInsight = warn ?? insights.first
        let topSignal = string(firstInsight?["text"]) ?? "No warnings yet."

        let verdict: Verdict
        if ended {
            verdict = .idle
        } else if (contextPct ?? 0) >= 0.80 {
            verdict = .intervene
        } else if lastTurnCost >= 0.50 {
            verdict = .intervene
        } else if warn != nil || (contextPct ?? 0) >= 0.65 {
            verdict = .watch
        } else {
            verdict = .healthy
        }

        return MeterSnapshot(
            connected: true,
            error: "",
            verdict: verdict,
            provider: provider,
            project: project,
            session: session,
            pricingNote: pricingNote,
            totalCost: totalCost,
            estimatedCost: estimatedCost,
            totalTokens: totalTokens,
            turns: turns,
            contextPct: contextPct,
            contextTokens: contextTokens,
            contextWindow: contextWindow,
            lastTurnCost: lastTurnCost,
            idleSeconds: idleSeconds,
            ended: ended,
            activityKind: activityKind,
            activityTitle: activityTitle,
            activityDetail: activityDetail,
            activityTime: activityTime,
            recommendationLabel: recommendationLabel,
            recommendationDetail: recommendationDetail,
            recommendationSeverity: recommendationSeverity,
            topSignal: topSignal
        )
    }

    var menuTitle: String {
        if !connected { return "Start Token Meter to see live cost." }
        return provider + projectSuffix
    }

    var projectSuffix: String {
        let trimmed = project.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return "" }
        return " - " + URL(fileURLWithPath: trimmed.replacingOccurrences(of: "~", with: NSHomeDirectory())).lastPathComponent
    }

    var statusTitle: String {
        if !connected { return verdict.prefix }
        return "\(verdict.prefix) \(formatMoney(totalCost)) \(contextLabel)"
    }

    var contextLabel: String {
        guard let pct = contextPct else { return "--% ctx" }
        return "\(Int((pct * 100).rounded()))% ctx"
    }

    var idleLabel: String {
        if ended { return "ended" }
        if idleSeconds < 60 { return "live - \(idleSeconds)s idle" }
        return "live - \(idleSeconds / 60)m idle"
    }

    var activitySummary: String {
        if activityDetail.isEmpty { return activityTitle }
        return "\(activityTitle) - \(activityDetail)"
    }

    var recommendationSummary: String {
        if recommendationDetail.isEmpty { return recommendationLabel }
        return "\(recommendationLabel) - \(recommendationDetail)"
    }
}

final class TokenMeterMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let stateURL = tokenMeterMenubarURL
    private let dashboardURL = tokenMeterDashboardURL
    private let menu = NSMenu()
    private let menuWidth: CGFloat = 340
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var snapshot = MeterSnapshot.disconnected("Waiting for http://127.0.0.1:8722/menubar")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        statusItem.menu = menu
        menu.delegate = self
        rebuildMenu()
        fetchState()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.fetchState()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuildMenu()
    }

    private func fetchState() {
        URLSession.shared.dataTask(with: stateURL) { [weak self] data, _, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let error = error {
                    self.snapshot = MeterSnapshot.disconnected(error.localizedDescription)
                    self.rebuildMenu()
                    return
                }
                guard
                    let data = data,
                    let obj = try? JSONSerialization.jsonObject(with: data),
                    let dict = obj as? [String: Any]
                else {
                    self.snapshot = MeterSnapshot.disconnected("Token Meter returned unreadable state.")
                    self.rebuildMenu()
                    return
                }
                self.snapshot = MeterSnapshot.fromJSON(dict)
                self.rebuildMenu()
            }
        }.resume()
    }

    private func rebuildMenu() {
        updateStatusTitle()
        menu.removeAllItems()

        addHeader()
        menu.addItem(.separator())

        if snapshot.connected {
            addActivityRow()
            addRecommendationRow()
            addMetricRow("Cost", "\(formatMoney(snapshot.totalCost))\(snapshot.estimatedCost ? " est" : "")")
            addMetricRow("Tokens", "\(formatCompactInt(snapshot.totalTokens)) - \(snapshot.turns) execs")
            addMetricRow("Context", "\(snapshot.contextLabel) - \(formatCompactInt(snapshot.contextTokens)) / \(formatCompactInt(snapshot.contextWindow))")
            addContextBar()
            addMetricRow("Last execution", formatMoney(snapshot.lastTurnCost))
        } else {
            addSignalRow("Connection", snapshot.error, color: .systemOrange)
        }

        menu.addItem(.separator())
        addMetricRow("Status", snapshot.verdict.label, valueColor: snapshot.verdict.color, strong: true)
        addSignalRow("Top signal", snapshot.topSignal)
        menu.addItem(.separator())

        addAction("Open Dashboard", #selector(openDashboard))
        addAction("Open Current Execution", #selector(openCurrentExecution), enabled: snapshot.connected)
        menu.addItem(.separator())
        addAction("Quit Token Meter Menubar", #selector(quit))
    }

    private func updateStatusTitle() {
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: snapshot.verdict.color,
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        ]
        statusItem.button?.attributedTitle = NSAttributedString(string: snapshot.statusTitle, attributes: attrs)
        statusItem.button?.toolTip = snapshot.connected ? snapshot.activitySummary : "Token Meter server is not reachable."
    }

    private func addHeader() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 48))
        let title = label(snapshot.menuTitle, frame: NSRect(x: 14, y: 23, width: menuWidth - 28, height: 18),
                          font: .systemFont(ofSize: 13, weight: .semibold),
                          color: .labelColor)
        let subtitle = label(snapshot.idleLabel, frame: NSRect(x: 14, y: 5, width: menuWidth - 28, height: 16),
                             font: .systemFont(ofSize: 12, weight: .regular),
                             color: .secondaryLabelColor)
        view.addSubview(title)
        view.addSubview(subtitle)
        addViewItem(view)
    }

    private func addActivityRow() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 58))
        let nameLabel = label("Now", frame: NSRect(x: 14, y: 36, width: 52, height: 15),
                              font: .systemFont(ofSize: 11.5, weight: .semibold),
                              color: .secondaryLabelColor)
        let dot = NSView(frame: NSRect(x: 54, y: 40, width: 7, height: 7))
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 3.5
        dot.layer?.backgroundColor = activityColor(snapshot.activityKind).cgColor
        let title = label(snapshot.activityTitle, frame: NSRect(x: 70, y: 32, width: menuWidth - 84, height: 19),
                          font: .systemFont(ofSize: 13, weight: .semibold),
                          color: .labelColor)
        let detailText = snapshot.activityDetail.isEmpty ? snapshot.activityTime : snapshot.activityDetail
        let detail = label(detailText, frame: NSRect(x: 70, y: 7, width: menuWidth - 84, height: 26),
                           font: .systemFont(ofSize: 12, weight: .regular),
                           color: .secondaryLabelColor)
        detail.lineBreakMode = .byWordWrapping
        detail.maximumNumberOfLines = 2
        view.addSubview(nameLabel)
        view.addSubview(dot)
        view.addSubview(title)
        view.addSubview(detail)
        addViewItem(view)
    }

    private func addRecommendationRow() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 52))
        let nameLabel = label("Action", frame: NSRect(x: 14, y: 31, width: 52, height: 15),
                              font: .systemFont(ofSize: 11.5, weight: .semibold),
                              color: .secondaryLabelColor)
        let valueLabel = label(snapshot.recommendationLabel, frame: NSRect(x: 70, y: 28, width: menuWidth - 84, height: 19),
                               font: .systemFont(ofSize: 13, weight: .semibold),
                               color: recommendationColor(snapshot.recommendationSeverity))
        let detail = label(snapshot.recommendationDetail, frame: NSRect(x: 70, y: 6, width: menuWidth - 84, height: 23),
                           font: .systemFont(ofSize: 12, weight: .regular),
                           color: .secondaryLabelColor)
        detail.lineBreakMode = .byWordWrapping
        detail.maximumNumberOfLines = 2
        view.addSubview(nameLabel)
        view.addSubview(valueLabel)
        view.addSubview(detail)
        addViewItem(view)
    }

    private func addMetricRow(_ name: String, _ value: String, valueColor: NSColor = .labelColor, strong: Bool = false) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 26))
        let nameLabel = label(name, frame: NSRect(x: 14, y: 5, width: 112, height: 16),
                              font: .systemFont(ofSize: 12, weight: .regular),
                              color: .secondaryLabelColor)
        let valueLabel = label(value, frame: NSRect(x: 126, y: 4, width: menuWidth - 140, height: 18),
                               font: .monospacedDigitSystemFont(ofSize: 12.5, weight: strong ? .semibold : .medium),
                               color: valueColor)
        valueLabel.alignment = .right
        view.addSubview(nameLabel)
        view.addSubview(valueLabel)
        addViewItem(view)
    }

    private func addContextBar() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 18))
        let track = NSView(frame: NSRect(x: 14, y: 7, width: menuWidth - 28, height: 5))
        track.wantsLayer = true
        track.layer?.cornerRadius = 2.5
        track.layer?.backgroundColor = NSColor.separatorColor.withAlphaComponent(0.45).cgColor
        let pct = max(0, min(snapshot.contextPct ?? 0, 1))
        let fill = NSView(frame: NSRect(x: 0, y: 0, width: (menuWidth - 28) * CGFloat(pct), height: 5))
        fill.wantsLayer = true
        fill.layer?.cornerRadius = 2.5
        fill.layer?.backgroundColor = contextColor(pct).cgColor
        track.addSubview(fill)
        view.addSubview(track)
        addViewItem(view)
    }

    private func addSignalRow(_ name: String, _ value: String, color: NSColor = .labelColor) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 50))
        let nameLabel = label(name, frame: NSRect(x: 14, y: 29, width: menuWidth - 28, height: 15),
                              font: .systemFont(ofSize: 11.5, weight: .semibold),
                              color: .secondaryLabelColor)
        let valueLabel = label(value, frame: NSRect(x: 14, y: 5, width: menuWidth - 28, height: 24),
                               font: .systemFont(ofSize: 12.5, weight: .semibold),
                               color: color)
        valueLabel.lineBreakMode = .byWordWrapping
        valueLabel.maximumNumberOfLines = 2
        view.addSubview(nameLabel)
        view.addSubview(valueLabel)
        addViewItem(view)
    }

    private func label(_ text: String, frame: NSRect, font: NSFont, color: NSColor) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.frame = frame
        field.font = font
        field.textColor = color
        field.lineBreakMode = .byTruncatingTail
        field.maximumNumberOfLines = 1
        field.isSelectable = false
        field.allowsDefaultTighteningForTruncation = true
        return field
    }

    private func addViewItem(_ view: NSView) {
        let item = NSMenuItem()
        item.view = view
        menu.addItem(item)
    }

    private func addAction(_ title: String, _ action: Selector, enabled: Bool = true) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        item.isEnabled = enabled
        menu.addItem(item)
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(dashboardURL)
    }

    @objc private func openCurrentExecution() {
        openDashboardPanel("activity")
    }

    private func openDashboardPanel(_ panel: String) {
        let url = URL(string: "http://127.0.0.1:8722/#\(panel)") ?? dashboardURL
        NSWorkspace.shared.open(url)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}

private func string(_ value: Any?) -> String? {
    value as? String
}

private func int(_ value: Any?) -> Int {
    if let value = value as? Int { return value }
    if let value = value as? Double { return Int(value) }
    if let value = value as? String { return Int(value) ?? 0 }
    return 0
}

private func double(_ value: Any?) -> Double {
    if let value = value as? Double { return value }
    if let value = value as? Int { return Double(value) }
    if let value = value as? String { return Double(value) ?? 0 }
    return 0
}

private func optionalDouble(_ value: Any?) -> Double? {
    if value == nil || value is NSNull { return nil }
    return double(value)
}

private func bool(_ value: Any?) -> Bool {
    if let value = value as? Bool { return value }
    if let value = value as? Int { return value != 0 }
    if let value = value as? String { return ["1", "true", "yes"].contains(value.lowercased()) }
    return false
}

private func formatMoney(_ value: Double) -> String {
    if abs(value) >= 1 {
        return String(format: "$%.2f", value)
    }
    return String(format: "$%.3f", value)
}

private func formatInt(_ value: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
}

private func formatCompactInt(_ value: Int) -> String {
    let magnitude = abs(value)
    let sign = value < 0 ? "-" : ""
    if magnitude >= 1_000_000_000 {
        return sign + compactScaled(Double(magnitude) / 1_000_000_000, suffix: "B")
    }
    if magnitude >= 1_000_000 {
        return sign + compactScaled(Double(magnitude) / 1_000_000, suffix: "M")
    }
    if magnitude >= 1_000 {
        return sign + compactScaled(Double(magnitude) / 1_000, suffix: "k")
    }
    return formatInt(value)
}

private func compactScaled(_ value: Double, suffix: String) -> String {
    let pattern: String
    if value >= 100 {
        pattern = "%.0f"
    } else if value >= 10 {
        pattern = "%.1f"
    } else {
        pattern = "%.2f"
    }
    var number = String(format: pattern, value)
    while number.contains(".") && number.hasSuffix("0") {
        number.removeLast()
    }
    if number.hasSuffix(".") {
        number.removeLast()
    }
    return number + suffix
}

private func activityColor(_ kind: String) -> NSColor {
    switch kind {
    case "tool_call": return .systemBlue
    case "tool_result": return .systemTeal
    case "reasoning": return .systemPurple
    case "complete": return .systemGreen
    case "message": return .labelColor
    case "offline": return .secondaryLabelColor
    default: return .systemOrange
    }
}

private func recommendationColor(_ severity: String) -> NSColor {
    switch severity {
    case "bad": return .systemRed
    case "warn": return .systemOrange
    case "good": return .systemGreen
    case "idle": return .secondaryLabelColor
    default: return .labelColor
    }
}

private func contextColor(_ pct: Double) -> NSColor {
    if pct >= 0.80 { return .systemRed }
    if pct >= 0.65 { return .systemOrange }
    return .systemGreen
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" {
    do {
        let data = try Data(contentsOf: tokenMeterMenubarURL)
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw NSError(domain: "TokenMeterMenuBar", code: 1, userInfo: [NSLocalizedDescriptionKey: "Response was not a JSON object."])
        }
        let snapshot = MeterSnapshot.fromJSON(dict)
        print(snapshot.statusTitle)
        print(snapshot.verdict.label)
        print(snapshot.activitySummary)
        print(snapshot.recommendationSummary)
        print(snapshot.topSignal)
        exit(0)
    } catch {
        fputs("Token Meter menubar smoke failed: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

let app = NSApplication.shared
let delegate = TokenMeterMenuBar()
app.delegate = delegate
app.run()
