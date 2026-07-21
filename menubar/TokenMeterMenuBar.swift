import Cocoa
import Foundation

private let tokenMeterMenubarURL = URL(string: "http://127.0.0.1:8722/menubar")!
private let tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#summary")!
private let pinnedSessionDefaultsKey = "TokenMeterPinnedSessionID"
private let tokenMeterDefaults = UserDefaults(suiteName: "com.token-meter.menubar") ?? .standard

// Token Meter accent blue (#00BCEB)
extension NSColor {
    static let tokenMeterBlue = NSColor(srgbRed: 0.0, green: 0.737, blue: 0.922, alpha: 1.0)
}

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
        case .healthy: return .tokenMeterBlue
        case .watch: return .tokenMeterBlue
        case .intervene: return .tokenMeterBlue
        case .idle: return .tokenMeterBlue
        case .disconnected: return .secondaryLabelColor
        }
    }

    static func fromKey(_ key: String?) -> Verdict? {
        switch key {
        case "healthy": return .healthy
        case "watch": return .watch
        case "intervene": return .intervene
        case "idle": return .idle
        case "disconnected": return .disconnected
        default: return nil
        }
    }

    static func fromLabel(_ label: String?) -> Verdict? {
        switch label {
        case "Healthy": return .healthy
        case "Watch closely": return .watch
        case "Intervene now": return .intervene
        case "Idle": return .idle
        case "Server offline": return .disconnected
        default: return nil
        }
    }
}

struct RecentSession {
    var id: String
    var provider: String
    var label: String
    var name: String
    var project: String

    static func fromJSON(_ dict: [String: Any]) -> RecentSession? {
        guard let id = string(dict["id"]), !id.isEmpty else { return nil }
        return RecentSession(
            id: id,
            provider: string(dict["provider"]) ?? "",
            label: string(dict["label"]) ?? "",
            name: string(dict["name"]) ?? "",
            project: string(dict["project"]) ?? ""
        )
    }

    var providerName: String {
        switch provider.lowercased() {
        case "codex": return "Codex"
        case "cursor": return "Cursor"
        default: return "Claude"
        }
    }

    var identifier: String {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanName.isEmpty { return cleanName }
        let cleanProject = project.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanProject.isEmpty {
            return URL(fileURLWithPath: cleanProject.replacingOccurrences(of: "~", with: NSHomeDirectory())).lastPathComponent
        }
        return String(id.prefix(12))
    }

    var menuTitle: String { "\(providerName) · \(identifier)" }

    var toolTip: String {
        [label.isEmpty ? providerName : label, project, String(id.prefix(12))]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    var symbolName: String {
        switch provider.lowercased() {
        case "codex": return "terminal"
        case "cursor": return "cursorarrow"
        default: return "sparkles"
        }
    }
}

struct MeterSnapshot {
    var connected: Bool
    var error: String
    var verdict: Verdict
    var verdictDetail: String
    var provider: String
    var model: String
    var project: String
    var session: String
    var pricingNote: String
    var costAvailable: Bool
    var tokensAvailable: Bool
    var cacheAvailable: Bool
    var throughputAvailable: Bool
    var totalCost: Double
    var estimatedCost: Bool
    var estimatedTokens: Bool
    var totalTokens: Int
    var turns: Int
    var outputTokensPerSecond: Double?
    var outputSpeedBasis: String
    var outputSpeedSamples: Int
    var outputSpeedCoverage: Double?
    var cacheInputShare: Double?
    var cacheTotalTokens: Int
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
            verdictDetail: "Token Meter server is not reachable.",
            provider: "Token Meter",
            model: "unknown",
            project: "",
            session: "",
            pricingNote: "",
            costAvailable: false,
            tokensAvailable: false,
            cacheAvailable: false,
            throughputAvailable: false,
            totalCost: 0,
            estimatedCost: false,
            estimatedTokens: false,
            totalTokens: 0,
            turns: 0,
            outputTokensPerSecond: nil,
            outputSpeedBasis: "unavailable",
            outputSpeedSamples: 0,
            outputSpeedCoverage: nil,
            cacheInputShare: nil,
            cacheTotalTokens: 0,
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
            recommendationDetail: "Run Token Meter to load live log state.",
            recommendationSeverity: "idle",
            topSignal: error
        )
    }

    static func fromJSON(_ dict: [String: Any]) -> MeterSnapshot {
        let source = dict["source"] as? [String: Any] ?? [:]
        let context = dict["context"] as? [String: Any] ?? [:]
        let cache = dict["cache"] as? [String: Any] ?? [:]
        let availability = dict["availability"] as? [String: Any] ?? [:]
        let insights = dict["insights"] as? [[String: Any]] ?? []

        let provider = string(source["label"]) ?? string(dict["provider"]) ?? "Token Meter"
        let model = string(dict["model"]) ?? string(source["model"]) ?? "unknown"
        let project = string(source["project"]) ?? string(dict["project"]) ?? ""
        let session = string(dict["session"]) ?? string(source["id"]) ?? ""
        let pricingNote = string(source["pricing_note"]) ?? ""
        let costAvailable = metricAvailable(availability, "cost")
        let tokensAvailable = metricAvailable(availability, "tokens")
        let cacheAvailable = metricAvailable(availability, "cache")
        let throughputAvailable = metricAvailable(availability, "throughput")
        let totalCost = double(dict["total_cost"])
        let estimatedCost = bool(dict["cost_approx"]) || bool(source["approximate_cost"])
        let estimatedTokens = bool(source["token_estimate"])
        let totalTokens = int(dict["total_tokens"])
        let turns = int(dict["turns"])
        let throughput = dict["throughput"] as? [String: Any] ?? [:]
        let outputTokensPerSecond = throughputAvailable && bool(throughput["available"])
            ? optionalDouble(throughput["output_tps"])
            : nil
        let outputSpeedBasis = string(throughput["basis"]) ?? "unavailable"
        let outputSpeedSamples = int(throughput["sample_count"])
        let outputSpeedCoverage = throughputAvailable && bool(throughput["available"])
            ? optionalDouble(throughput["timing_coverage"])
            : nil
        let cacheInputShare = cacheAvailable ? optionalDouble(cache["input_share"]) : nil
        let cacheTotalTokens = int(cache["total"])
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
        let verdictInfo = dict["verdict"] as? [String: Any] ?? [:]
        let serverVerdict = Verdict.fromKey(string(verdictInfo["key"])) ?? Verdict.fromLabel(string(verdictInfo["label"]))
        let serverVerdictDetail = string(verdictInfo["detail"])

        let warn = insights.first { string($0["kind"]) == "warn" }
        let firstInsight = warn ?? insights.first
        let topSignal = string(firstInsight?["text"]) ?? "No warnings yet."

        let verdict: Verdict
        if let serverVerdict = serverVerdict {
            verdict = serverVerdict
        } else if ended {
            verdict = .idle
        } else if (contextPct ?? 0) >= 0.85 {
            verdict = .intervene
        } else if costAvailable && lastTurnCost >= 0.50 {
            verdict = .intervene
        } else if (contextPct ?? 0) >= 0.70 || recommendationSeverity == "warn" {
            verdict = .watch
        } else {
            verdict = .healthy
        }

        return MeterSnapshot(
            connected: true,
            error: "",
            verdict: verdict,
            verdictDetail: serverVerdictDetail ?? MeterSnapshot.verdictFallbackDetail(
                verdict,
                contextPct: contextPct,
                lastTurnCost: lastTurnCost,
                costAvailable: costAvailable
            ),
            provider: provider,
            model: model,
            project: project,
            session: session,
            pricingNote: pricingNote,
            costAvailable: costAvailable,
            tokensAvailable: tokensAvailable,
            cacheAvailable: cacheAvailable,
            throughputAvailable: throughputAvailable,
            totalCost: totalCost,
            estimatedCost: estimatedCost,
            estimatedTokens: estimatedTokens,
            totalTokens: totalTokens,
            turns: turns,
            outputTokensPerSecond: outputTokensPerSecond,
            outputSpeedBasis: outputSpeedBasis,
            outputSpeedSamples: outputSpeedSamples,
            outputSpeedCoverage: outputSpeedCoverage,
            cacheInputShare: cacheInputShare,
            cacheTotalTokens: cacheTotalTokens,
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

    static func verdictFallbackDetail(_ verdict: Verdict, contextPct: Double?, lastTurnCost: Double, costAvailable: Bool) -> String {
        let pct = Int(((contextPct ?? 0) * 100).rounded())
        switch verdict {
        case .healthy:
            return "Context is \(pct)% and no operational warning needs intervention."
        case .watch:
            return "Context is \(pct)% or an operational warning is active."
        case .intervene:
            if (contextPct ?? 0) >= 0.85 {
                return "Context is \(pct)% of the model window; compact now."
            }
            if costAvailable {
                return "Last execution cost \(formatMoney(lastTurnCost)); review the spike before continuing."
            }
            return "An operational warning needs intervention."
        case .idle:
            return "This is a frozen log view; return to live to follow newest activity."
        case .disconnected:
            return "Token Meter server is not reachable."
        }
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
        return "\(costLabel) · \(contextLabel) · \(outputSpeedLabel) · \(model)"
    }

    var costLabel: String { costAvailable ? "\(formatMoney(totalCost))\(estimatedCost ? " est" : "")" : "--" }

    var contextLabel: String {
        guard let pct = contextPct else { return "--% ctx" }
        return "\(Int((pct * 100).rounded()))% ctx"
    }

    var cacheLabel: String {
        guard cacheAvailable else { return "unavailable" }
        guard cacheTotalTokens > 0 else { return "no cache yet" }
        let share = Int(((cacheInputShare ?? 0) * 100).rounded())
        return "\(share)% input cached - \(formatCompactInt(cacheTotalTokens))"
    }

    var outputSpeedLabel: String {
        guard let rate = outputTokensPerSecond, rate > 0 else { return "-- tok/s" }
        return "\(formatTokenRate(rate)) tok/s\(estimatedTokens ? " est" : "")"
    }

    var outputSpeedTooltip: String {
        guard throughputAvailable else {
            return "Observed output throughput is unavailable because this trace does not expose output-token counts."
        }
        guard outputTokensPerSecond != nil else {
            return "Observed output throughput is unavailable because this log has no completed timed samples."
        }
        let basis = outputSpeedBasis == "tool_free" ? "tool-free timing" : "end-to-end timing"
        let sampleWord = outputSpeedSamples == 1 ? "sample" : "samples"
        let coverage = Int(((outputSpeedCoverage ?? 0) * 100).rounded())
        let caveat = outputSpeedBasis == "tool_free"
            ? "Tool execution time is excluded."
            : "Tool execution time may be included."
        let estimate = estimatedTokens ? " Cursor output uses trace-visible text estimated at four characters per token." : ""
        return "Observed output throughput from \(outputSpeedSamples) timed \(sampleWord) using \(basis); \(coverage)% output coverage. \(caveat)\(estimate)"
    }

    var idleLabel: String {
        if ended { return "pinned log" }
        if idleSeconds < 60 { return "live - \(idleSeconds)s idle" }
        return "live - \(idleSeconds / 60)m idle"
    }

    var statusTooltip: String {
        if !connected { return "Token Meter server is not reachable." }
        return "Cost: \(costLabel)\nContext: \(contextLabel)\nOutput speed: \(outputSpeedLabel)\nModel: \(model)"
    }
}

final class TokenMeterMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let stateURL = tokenMeterMenubarURL
    private let dashboardURL = tokenMeterDashboardURL
    private let menu = NSMenu()
    private let menuWidth: CGFloat = 340
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var pinnedSessionID = tokenMeterDefaults.string(forKey: pinnedSessionDefaultsKey)
    private var recentSessions: [RecentSession] = []
    private var menuIsOpen = false
    private var menuRefreshPending = false
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

    func menuWillOpen(_ menu: NSMenu) {
        menuIsOpen = true
    }

    func menuDidClose(_ menu: NSMenu) {
        menuIsOpen = false
        if menuRefreshPending {
            menuRefreshPending = false
            rebuildMenu()
        }
    }

    private func fetchState() {
        let requestedSessionID = pinnedSessionID
        let requestURL = menubarRequestURL(sessionID: requestedSessionID)
        var request = URLRequest(
            url: requestURL,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 5.0
        )
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")
        URLSession.shared.dataTask(with: request) { [weak self] data, _, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard requestedSessionID == self.pinnedSessionID else { return }
                if let error = error {
                    self.snapshot = MeterSnapshot.disconnected(error.localizedDescription)
                    self.refreshMenu()
                    return
                }
                guard
                    let data = data,
                    let obj = try? JSONSerialization.jsonObject(with: data),
                    let dict = obj as? [String: Any]
                else {
                    self.snapshot = MeterSnapshot.disconnected("Token Meter returned unreadable state.")
                    self.refreshMenu()
                    return
                }
                let selection = dict["selection"] as? [String: Any] ?? [:]
                if bool(selection["missing"]) {
                    self.persistPinnedSession(nil)
                }
                self.recentSessions = (dict["recent_sessions"] as? [[String: Any]] ?? [])
                    .compactMap(RecentSession.fromJSON)
                self.snapshot = MeterSnapshot.fromJSON(dict)
                self.refreshMenu()
            }
        }.resume()
    }

    private func menubarRequestURL(sessionID: String?) -> URL {
        guard let sessionID = sessionID, !sessionID.isEmpty,
              var components = URLComponents(url: stateURL, resolvingAgainstBaseURL: false)
        else { return stateURL }
        components.queryItems = [URLQueryItem(name: "session", value: sessionID)]
        return components.url ?? stateURL
    }

    private func refreshMenu() {
        if menuIsOpen {
            updateStatusTitle()
            menuRefreshPending = true
        } else {
            rebuildMenu()
        }
    }

    private func rebuildMenu() {
        updateStatusTitle()
        menu.removeAllItems()

        addHeader()
        menu.addItem(.separator())

        addAction("Open Dashboard", #selector(openDashboard))
        addAction("Open Daily Brief", #selector(openDailyBrief))
        addAction("Open Trace", #selector(openTrace), enabled: snapshot.connected)
        addAction("Open Tools", #selector(openToolsAndSkills))
        menu.addItem(.separator())

        addSessionPicker()
        menu.addItem(.separator())

        if snapshot.connected {
            addMetricRow("Model", snapshot.model)
            addMetricRow("Cost", snapshot.costAvailable
                         ? "\(formatMoney(snapshot.totalCost))\(snapshot.estimatedCost ? " est" : "")"
                         : "--", toolTip: snapshot.estimatedTokens
                            ? snapshot.pricingNote
                            : (snapshot.costAvailable ? nil : "This trace does not expose enough local pricing evidence."))
            addMetricRow("Context", "\(snapshot.contextLabel) - \(formatCompactInt(snapshot.contextTokens)) / \(formatCompactInt(snapshot.contextWindow))",
                         toolTip: "Context watch starts at 70%; intervene starts at 85%.")
            addContextBar()
            addMetricRow("Output speed", snapshot.outputSpeedLabel, toolTip: snapshot.outputSpeedTooltip)
            addMetricRow("Tokens", snapshot.tokensAvailable
                         ? "\(formatCompactInt(snapshot.totalTokens))\(snapshot.estimatedTokens ? " est" : "") - \(snapshot.turns) execs"
                         : "-- - \(snapshot.turns) execs",
                         toolTip: snapshot.estimatedTokens
                            ? "Cursor tokens are local context-and-visible-output proxies; cache and hidden model work are excluded."
                            : (snapshot.tokensAvailable ? nil : "Input and output tokens are unavailable for this trace."))
            addMetricRow("Cache", snapshot.cacheLabel)
            addMetricRow("Last execution", snapshot.costAvailable
                         ? "\(formatMoney(snapshot.lastTurnCost))\(snapshot.estimatedCost ? " est" : "")"
                         : "--")
        } else {
            addSignalRow("Connection", snapshot.error, color: .tokenMeterBlue)
        }

        menu.addItem(.separator())
        addAction("Quit Token Meter Menubar", #selector(quit))
    }

    private func updateStatusTitle() {
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: NSColor.labelColor,
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        ]
        statusItem.button?.attributedTitle = NSAttributedString(string: snapshot.statusTitle, attributes: attrs)
        statusItem.button?.toolTip = snapshot.statusTooltip
    }

    private func addHeader() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 48))
        let title = label(snapshot.menuTitle, frame: NSRect(x: 14, y: 23, width: menuWidth - 28, height: 18),
                          font: .systemFont(ofSize: 13, weight: .semibold),
                          color: .labelColor)
        let subtitleText = pinnedSessionID == nil ? snapshot.idleLabel : "pinned · \(snapshot.idleLabel)"
        let subtitle = label(subtitleText, frame: NSRect(x: 14, y: 5, width: menuWidth - 28, height: 16),
                             font: .systemFont(ofSize: 12, weight: .regular),
                             color: .secondaryLabelColor)
        view.addSubview(title)
        view.addSubview(subtitle)
        addViewItem(view)
    }

    private func addSessionPicker() {
        let heading = NSMenuItem(title: "Recent sessions", action: nil, keyEquivalent: "")
        heading.isEnabled = false
        menu.addItem(heading)

        let follow = NSMenuItem(title: "Follow Latest", action: #selector(followLatest), keyEquivalent: "")
        follow.target = self
        follow.state = pinnedSessionID == nil ? .on : .off
        follow.toolTip = "Automatically follow whichever session was active most recently."
        follow.image = menuSymbol("arrow.triangle.2.circlepath", description: "Follow latest")
        menu.addItem(follow)

        for session in recentSessions {
            let item = NSMenuItem(title: session.menuTitle, action: #selector(pinSession(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = session.id
            item.state = pinnedSessionID == session.id ? .on : .off
            item.toolTip = "Click to pin · \(session.toolTip)"
            item.image = menuSymbol(session.symbolName, description: session.providerName)
            menu.addItem(item)
        }
    }

    private func menuSymbol(_ name: String, description: String) -> NSImage? {
        if #available(macOS 11.0, *) {
            let image = NSImage(systemSymbolName: name, accessibilityDescription: description)
            image?.isTemplate = true
            return image
        }
        return nil
    }

    private func addMetricRow(_ name: String, _ value: String, valueColor: NSColor = .labelColor, strong: Bool = false, toolTip: String? = nil) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 26))
        view.toolTip = toolTip
        let nameLabel = label(name, frame: NSRect(x: 14, y: 5, width: 112, height: 16),
                              font: .systemFont(ofSize: 12, weight: .regular),
                              color: .secondaryLabelColor)
        let valueLabel = label(value, frame: NSRect(x: 126, y: 4, width: menuWidth - 140, height: 18),
                               font: .monospacedDigitSystemFont(ofSize: 12.5, weight: strong ? .semibold : .medium),
                               color: valueColor)
        valueLabel.alignment = .right
        nameLabel.toolTip = toolTip
        valueLabel.toolTip = toolTip
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

    private func persistPinnedSession(_ sessionID: String?) {
        pinnedSessionID = sessionID
        if let sessionID = sessionID {
            tokenMeterDefaults.set(sessionID, forKey: pinnedSessionDefaultsKey)
        } else {
            tokenMeterDefaults.removeObject(forKey: pinnedSessionDefaultsKey)
        }
    }

    @objc private func followLatest() {
        persistPinnedSession(nil)
        refreshMenu()
        fetchState()
    }

    @objc private func pinSession(_ sender: NSMenuItem) {
        guard let sessionID = sender.representedObject as? String, !sessionID.isEmpty else { return }
        persistPinnedSession(sessionID)
        refreshMenu()
        fetchState()
    }

    @objc private func openDashboard() {
        openDashboardPanel("summary")
    }

    @objc private func openDailyBrief() {
        openDashboardPanel("daily", includePinnedSession: false)
    }

    @objc private func openTrace() {
        openDashboardPanel("activity")
    }

    @objc private func openToolsAndSkills() {
        openDashboardPanel("capabilities", includePinnedSession: false)
    }

    private func openDashboardPanel(_ panel: String, includePinnedSession: Bool = true) {
        var components = URLComponents(string: "http://127.0.0.1:8722/")
        if includePinnedSession, let sessionID = pinnedSessionID, !sessionID.isEmpty {
            components?.path = "/sessions/\(sessionID)"
        }
        components?.fragment = panel
        NSWorkspace.shared.open(components?.url ?? dashboardURL)
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

private func metricAvailable(_ availability: [String: Any], _ metric: String) -> Bool {
    guard availability.keys.contains(metric) else { return true }
    return bool(availability[metric])
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

private func formatTokenRate(_ value: Double) -> String {
    if value >= 100 { return String(format: "%.0f", value) }
    if value >= 10 { return String(format: "%.1f", value) }
    return String(format: "%.2f", value)
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

private func contextColor(_ pct: Double) -> NSColor {
    if pct >= 0.85 { return .tokenMeterBlue }
    if pct >= 0.70 { return .tokenMeterBlue }
    return .tokenMeterBlue
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
        print(snapshot.outputSpeedLabel)
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
