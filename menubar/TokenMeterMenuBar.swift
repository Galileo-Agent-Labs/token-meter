import Cocoa
import Carbon.HIToolbox
import Foundation

private let tokenMeterMenubarURL = URL(string: "http://127.0.0.1:8722/menubar")!
private let tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#sessions")!
private let tokenMeterBudgetSettingsURL = URL(string: "http://127.0.0.1:8722/#settings-budgets")!
private let pinnedSessionDefaultsKey = "TokenMeterPinnedSessionID"
private let selectedTabDefaultsKey = "TokenMeterSelectedTab"
private let titleModeDefaultsKey = "TokenMeterTitleMode"
private let titleMetricsDefaultsKey = "TokenMeterTitleMetrics"
private let quotaAlertsEnabledDefaultsKey = "TokenMeterQuotaAlertsEnabled"
private let quotaAlertThresholdDefaultsKey = "TokenMeterQuotaAlertThreshold"
private let quotaNotificationStatesDefaultsKey = "TokenMeterQuotaNotificationStates"
private let budgetNotificationStatesDefaultsKey = "TokenMeterBudgetNotificationStates"
private let budgetExceededMonthsDefaultsKey = "TokenMeterBudgetExceededNotificationMonths"
private let statusDisplayModeDefaultsKey = "TokenMeterStatusDisplayMode"
private let globalShortcutDefaultsKey = "TokenMeterGlobalShortcut"
private let customShortcutKeyCodeDefaultsKey = "TokenMeterCustomShortcutKeyCode"
private let customShortcutModifiersDefaultsKey = "TokenMeterCustomShortcutModifiers"
private let tokenMeterMenubarBundleIdentifier = "com.token-meter.menubar"
private let statusItemAutosaveName = "TokenMeterPrimaryStatusItem"
private let statusItemPreferredPositionDefaultsKey = "NSStatusItem Preferred Position \(statusItemAutosaveName)"
private let statusItemInitialPreferredPosition = 50
private let tokenMeterDefaults: UserDefaults = {
    if Bundle.main.bundleIdentifier == tokenMeterMenubarBundleIdentifier { return .standard }
    return UserDefaults(suiteName: tokenMeterMenubarBundleIdentifier) ?? .standard
}()

// Token Meter accent blue (#00BCEB)
extension NSColor {
    static let tokenMeterBlue = NSColor(srgbRed: 0.0, green: 0.737, blue: 0.922, alpha: 1.0)
    static let tokenMeterInk = NSColor(srgbRed: 0.035, green: 0.061, blue: 0.078, alpha: 1.0)
    static let tokenMeterPlate = NSColor(srgbRed: 0.064, green: 0.102, blue: 0.125, alpha: 1.0)
    static let tokenMeterLine = NSColor(srgbRed: 0.20, green: 0.28, blue: 0.31, alpha: 1.0)
    static let tokenMeterMuted = NSColor(srgbRed: 0.61, green: 0.69, blue: 0.72, alpha: 1.0)
}

private func splunkWordmarkImage() -> NSImage? {
    let source = URL(fileURLWithPath: #filePath)
    let root = source.deletingLastPathComponent().deletingLastPathComponent()
    let asset = root.appendingPathComponent("assets/brand/logo-splunk-acc-rgb-w.png")
    guard let image = NSImage(contentsOf: asset) else { return nil }
    image.accessibilityDescription = "Splunk"
    return image
}

private func splunkChevronImage(accessibilityDescription: String = "Splunk Token Meter") -> NSImage {
    let size = NSSize(width: 18, height: 18)
    let image = NSImage(size: size, flipped: false) { rect in
        let sourceWidth: CGFloat = 37.03
        let sourceHeight: CGFloat = 44.58
        let inset: CGFloat = 2
        let scale = min(
            (rect.width - inset * 2) / sourceWidth,
            (rect.height - inset * 2) / sourceHeight
        )
        let origin = NSPoint(
            x: rect.midX - sourceWidth * scale / 2,
            y: rect.midY - sourceHeight * scale / 2
        )
        func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
            NSPoint(x: origin.x + x * scale, y: origin.y + (sourceHeight - y) * scale)
        }

        let chevron = NSBezierPath()
        chevron.move(to: point(37.03, 26.16))
        chevron.line(to: point(37.03, 18.58))
        chevron.line(to: point(0, 0))
        chevron.line(to: point(0, 8.32))
        chevron.line(to: point(28.70, 22.29))
        chevron.line(to: point(0, 36.44))
        chevron.line(to: point(0, 44.58))
        chevron.line(to: point(37.03, 26.18))
        chevron.close()
        NSColor.black.setFill()
        chevron.fill()
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = accessibilityDescription
    return image
}

private func statusTitleImage(_ title: String) -> NSImage {
    let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
    let attributedTitle = NSAttributedString(
        string: title,
        attributes: [
            .foregroundColor: NSColor.black,
            .font: font,
        ]
    )
    let measuredSize = attributedTitle.size()
    let imageSize = NSSize(width: ceil(measuredSize.width), height: 18)
    let image = NSImage(size: imageSize, flipped: false) { rect in
        attributedTitle.draw(at: NSPoint(
            x: 0,
            y: floor((rect.height - measuredSize.height) / 2)
        ))
        return true
    }
    // The glyphs are a mask. macOS owns the final black/white tint, using the
    // actual menu-bar material rather than this process's effective appearance.
    image.isTemplate = true
    image.accessibilityDescription = title
    return image
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

enum MenuTab: String, CaseIterable {
    case run
    case claude
    case codex
    case cursor

    var title: String {
        switch self {
        case .run: return "Run"
        case .claude: return "Claude"
        case .codex: return "Codex"
        case .cursor: return "Cursor"
        }
    }
}

enum TitleMetric: String, CaseIterable {
    case cost
    case speed
    case context
    case model
    case limits

    var title: String {
        switch self {
        case .cost: return "Cost"
        case .speed: return "Output speed"
        case .context: return "Context"
        case .model: return "Model"
        case .limits: return "Limits"
        }
    }
}

enum StatusDisplayMode: String, CaseIterable {
    case automatic
    case text
    case icon

    var title: String {
        switch self {
        case .automatic: return "Automatic"
        case .text: return "Cost + speed text"
        case .icon: return "Icon only"
        }
    }
}

enum MenuBarShortcut: String, CaseIterable {
    case controlOptionT
    case commandOptionT
    case controlOptionM
    case custom
    case off

    var title: String {
        switch self {
        case .controlOptionT: return "⌃⌥T"
        case .commandOptionT: return "⌥⌘T"
        case .controlOptionM: return "⌃⌥M"
        case .custom: return "Custom…"
        case .off: return "Off"
        }
    }

    var keyCode: UInt32? {
        switch self {
        case .controlOptionT, .commandOptionT: return 17 // T on the macOS US keyboard layout.
        case .controlOptionM: return 46 // M on the macOS US keyboard layout.
        case .custom: return nil
        case .off: return nil
        }
    }

    var modifiers: UInt32 {
        switch self {
        case .controlOptionT, .controlOptionM: return UInt32(controlKey | optionKey)
        case .commandOptionT: return UInt32(cmdKey | optionKey)
        case .custom: return 0
        case .off: return 0
        }
    }
}

struct QuotaPace {
    var state: String
    var summary: String

    static func fromJSON(_ dict: [String: Any]?) -> QuotaPace? {
        guard let dict = dict, let summary = string(dict["summary"]), !summary.isEmpty else { return nil }
        return QuotaPace(state: string(dict["state"]) ?? "on_pace", summary: summary)
    }
}

struct QuotaWindow {
    var id: String
    var kind: String
    var label: String
    var usedPercent: Double
    var windowSeconds: Int?
    var resetAt: Date?
    var pace: QuotaPace?

    static func fromJSON(_ dict: [String: Any]) -> QuotaWindow? {
        guard let id = string(dict["id"]), !id.isEmpty,
              let usedPercent = optionalDouble(dict["used_percent"])
        else { return nil }
        let duration = optionalDouble(dict["window_seconds"]).map(Int.init)
        let resetAt = optionalDouble(dict["reset_at"]).map(Date.init(timeIntervalSince1970:))
        return QuotaWindow(
            id: id,
            kind: string(dict["kind"]) ?? "extra",
            label: string(dict["label"]) ?? "Quota",
            usedPercent: max(0, min(100, usedPercent)),
            windowSeconds: duration,
            resetAt: resetAt,
            pace: QuotaPace.fromJSON(dict["pace"] as? [String: Any])
        )
    }

    var compactKind: String {
        switch kind {
        case "session": return "session"
        case "weekly": return "weekly"
        case "monthly": return "monthly"
        default: return label.lowercased()
        }
    }

    var percentLabel: String { "\(Int(usedPercent.rounded()))%" }

    var resetLabel: String {
        guard let resetAt = resetAt else { return "reset time unavailable" }
        let remaining = resetAt.timeIntervalSinceNow
        if remaining <= 0 { return "reset pending" }
        return "resets in \(formatCompactDuration(remaining))"
    }
}

struct ProviderQuota {
    var id: String
    var label: String
    var status: String
    var plan: String
    var source: String
    var provenance: String
    var ageSeconds: Int?
    var stale: Bool
    var error: String
    var coverageNote: String
    var windows: [QuotaWindow]

    static func fromJSON(_ dict: [String: Any]) -> ProviderQuota? {
        guard let id = string(dict["id"]), !id.isEmpty else { return nil }
        return ProviderQuota(
            id: id,
            label: string(dict["label"]) ?? id.capitalized,
            status: string(dict["status"]) ?? "unavailable",
            plan: string(dict["plan"]) ?? "",
            source: string(dict["source"]) ?? "Provider account",
            provenance: string(dict["provenance"]) ?? "unavailable",
            ageSeconds: optionalDouble(dict["age_seconds"]).map(Int.init),
            stale: bool(dict["stale"]),
            error: string(dict["error"]) ?? "",
            coverageNote: string(dict["coverage_note"]) ?? "",
            windows: (dict["windows"] as? [[String: Any]] ?? []).compactMap(QuotaWindow.fromJSON)
        )
    }

    var fresh: Bool { status == "ok" && !stale }

    var highestWindow: QuotaWindow? {
        windows.max { $0.usedPercent < $1.usedPercent }
    }

    var freshnessLabel: String {
        if stale {
            return ageSeconds.map { "Stale · \(formatCompactDuration(Double($0))) old" } ?? "Stale"
        }
        guard let ageSeconds = ageSeconds else { return status == "loading" ? "Loading" : "Not refreshed" }
        if ageSeconds < 10 { return "Updated now" }
        return "Updated \(formatCompactDuration(Double(ageSeconds))) ago"
    }
}

struct QuotaNotificationState: Codable {
    var lastUsedPercent: Double
    var resetAt: TimeInterval?
    var firedThresholds: Set<Int>
}

struct BudgetScope {
    var id: String
    var label: String
    var spend: Double
    var budget: Double
    var percent: Double
}

struct MonthlyBudget {
    var month: String
    var configured: Bool
    var spend: Double
    var budget: Double
    var percent: Double
    var lowerBound: Bool
    var nativeNotifications: Bool
    var thresholds: [Int]
    var scopes: [BudgetScope]

    static func fromJSON(_ dict: [String: Any]?) -> MonthlyBudget? {
        guard let dict = dict else { return nil }
        let configured = bool(dict["configured"])
        let settings = dict["settings"] as? [String: Any] ?? [:]
        let thresholds = (settings["thresholds"] as? [Any] ?? [])
            .compactMap { optionalDouble($0).map(Int.init) }
        let runtimes = (dict["runtimes"] as? [[String: Any]] ?? []).compactMap { row -> BudgetScope? in
            guard optionalDouble(row["allocation"]) ?? 0 > 0,
                  let percent = optionalDouble(row["percent"]),
                  let id = string(row["provider"])
            else { return nil }
            return BudgetScope(
                id: id,
                label: string(row["label"]) ?? id.capitalized,
                spend: double(row["spend"]),
                budget: double(row["allocation"]),
                percent: percent * 100
            )
        }
        var scopes = runtimes
        if configured {
            scopes.insert(BudgetScope(
                id: "overall",
                label: "Overall",
                spend: double(dict["spend"]),
                budget: double(dict["budget"]),
                percent: double(dict["percent"]) * 100
            ), at: 0)
        }
        return MonthlyBudget(
            month: string(dict["month"]) ?? "",
            configured: configured,
            spend: double(dict["spend"]),
            budget: double(dict["budget"]),
            percent: double(dict["percent"]) * 100,
            lowerBound: bool(dict["lower_bound"]),
            nativeNotifications: settings["native_notifications"] == nil
                ? true : bool(settings["native_notifications"]),
            thresholds: thresholds.isEmpty ? [80, 90, 100] : thresholds,
            scopes: scopes
        )
    }

    var compactLabel: String {
        guard configured else { return "Budget not set" }
        if let scope = exceededRuntimeScopes.first {
            return "⚠︎ \(scope.label) · \(Int(scope.percent.rounded()))%"
        }
        if exceeded { return "⚠︎ Overall · \(Int(percent.rounded()))%" }
        let prefix = lowerBound ? "≥" : ""
        return "\(prefix)\(Int(percent.rounded()))% budget"
    }

    var exceeded: Bool { configured && percent >= 100 }
    var exceededRuntimeScopes: [BudgetScope] {
        scopes.filter { $0.id != "overall" && $0.percent >= 100 }
    }
    var anyExceeded: Bool { exceeded || !exceededRuntimeScopes.isEmpty }

    var toolTip: String {
        guard configured else { return "Monthly budget is not configured." }
        let prefix = lowerBound ? "At least " : ""
        let overall = "\(prefix)\(formatMoney(spend)) of \(formatMoney(budget)) recorded for \(month)."
        let runtimeWarnings = exceededRuntimeScopes.map {
            "\($0.label): \(formatMoney($0.spend)) of \(formatMoney($0.budget)) (\(Int($0.percent.rounded()))%)."
        }
        return ([overall] + runtimeWarnings).joined(separator: "\n")
    }
}

struct BudgetNotificationState: Codable {
    var month: String
    var lastPercent: Double
    var firedThresholds: Set<Int>
}

struct RecentSession {
    var id: String
    var provider: String
    var label: String
    var name: String

    static func fromJSON(_ dict: [String: Any]) -> RecentSession? {
        guard let id = string(dict["id"]), !id.isEmpty else { return nil }
        return RecentSession(
            id: id,
            provider: string(dict["provider"]) ?? "",
            label: string(dict["label"]) ?? "",
            name: string(dict["name"]) ?? ""
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
        return String(id.prefix(12))
    }

    var toolTip: String {
        [identifier, label.isEmpty ? providerName : label, String(id.prefix(12))]
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
    var contextPulse: [Double]
    var selectedSessionID: String
    var pinnedSession: Bool

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
            topSignal: error,
            contextPulse: [],
            selectedSessionID: "",
            pinnedSession: false
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
        let contextPulse = (dict["context_pulse"] as? [Any] ?? []).compactMap { value -> Double? in
            guard let value = optionalDouble(value) else { return nil }
            return max(0, min(1, value))
        }
        let selection = dict["selection"] as? [String: Any] ?? [:]
        let selectedSessionID = string(selection["selected_id"]) ?? string(source["id"]) ?? ""
        let pinnedSession = bool(selection["pinned"])

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
            topSignal: topSignal,
            contextPulse: contextPulse,
            selectedSessionID: selectedSessionID,
            pinnedSession: pinnedSession
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

private final class ExecutionTraceView: NSView {
    private let values: [Double]

    init(values: [Double]) {
        self.values = values
        super.init(frame: NSRect(x: 0, y: 0, width: 320, height: 34))
        setAccessibilityElement(true)
        setAccessibilityLabel("Measured execution trace")
        setAccessibilityValue(values.isEmpty ? "No measured context history" : "\(values.count) recent measured executions")
        toolTip = "Measured context percentage from recent completed executions."
    }

    required init?(coder: NSCoder) { nil }

    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: 34) }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard !values.isEmpty else {
            let note = "No measured context yet"
            note.draw(
                at: NSPoint(x: 0, y: 10),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 11, weight: .medium),
                    .foregroundColor: NSColor.tokenMeterMuted,
                ]
            )
            return
        }

        let inset: CGFloat = 5
        let width = max(1, bounds.width - inset * 2)
        let baseline = bounds.midY
        let amplitude = max(8, bounds.height * 0.34)
        let step = values.count > 1 ? width / CGFloat(values.count - 1) : 0
        let point = { (index: Int, value: Double) -> NSPoint in
            NSPoint(x: inset + CGFloat(index) * step, y: baseline + CGFloat(value - 0.5) * amplitude * 2)
        }

        NSColor.tokenMeterLine.withAlphaComponent(0.72).setStroke()
        let baselinePath = NSBezierPath()
        baselinePath.move(to: NSPoint(x: inset, y: baseline))
        baselinePath.line(to: NSPoint(x: bounds.maxX - inset, y: baseline))
        baselinePath.lineWidth = 1
        baselinePath.stroke()

        let trace = NSBezierPath()
        for (index, value) in values.enumerated() {
            let position = point(index, value)
            if index == 0 { trace.move(to: position) } else { trace.line(to: position) }
        }
        NSColor.tokenMeterBlue.withAlphaComponent(0.78).setStroke()
        trace.lineWidth = 1.5
        trace.lineJoinStyle = .round
        trace.stroke()

        for (index, value) in values.enumerated() {
            let position = point(index, value)
            let dotSize: CGFloat = index == values.count - 1 ? 7 : 3.5
            let dotRect = NSRect(x: position.x - dotSize / 2, y: position.y - dotSize / 2, width: dotSize, height: dotSize)
            let color = contextSignalColor(value)
            color.withAlphaComponent(index == values.count - 1 ? 1 : 0.45).setFill()
            NSBezierPath(ovalIn: dotRect).fill()
        }

        let latest = point(values.count - 1, values[values.count - 1])
        NSColor.tokenMeterInk.setFill()
        NSBezierPath(ovalIn: NSRect(x: latest.x - 2, y: latest.y - 2, width: 4, height: 4)).fill()
    }
}

private final class LevelBarView: NSView {
    private let value: Double
    private let color: NSColor

    init(value: Double, color: NSColor = .tokenMeterBlue) {
        self.value = max(0, min(1, value))
        self.color = color
        super.init(frame: NSRect(x: 0, y: 0, width: 250, height: 7))
        setAccessibilityElement(true)
        setAccessibilityLabel("Usage level")
        setAccessibilityValue("\(Int((self.value * 100).rounded())) percent")
    }

    required init?(coder: NSCoder) { nil }

    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: 7) }

    override func draw(_ dirtyRect: NSRect) {
        let track = bounds
        NSColor.tokenMeterLine.withAlphaComponent(0.68).setFill()
        NSBezierPath(roundedRect: track, xRadius: 3.5, yRadius: 3.5).fill()
        let fill = NSRect(x: track.minX, y: track.minY, width: track.width * CGFloat(value), height: track.height)
        guard fill.width > 0 else { return }
        color.setFill()
        NSBezierPath(roundedRect: fill, xRadius: 3.5, yRadius: 3.5).fill()
    }
}

// RUN SLIP CONTRACT
// THESIS: Make the active run read like a precise field record, not a compressed dashboard.
// OWN-WORLD: Charcoal paper, a single cyan execution trace, hairline dividers, and system type used with editorial restraint.
// STORY: Identify the run, read its changing context, then decide whether its spend, pace, and allowance warrant attention.
// FIRST VIEWPORT: Identity and quiet scope rail lead into one full-width measured trace, a three-part ledger, recent run links, and a text action footer.
// FORM: Native transient popover; seed run-pulse-wide-band. FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
private final class RunPulsePopoverController: NSViewController {
    private enum Layout {
        static let width: CGFloat = 360
        static let maxHeight: CGFloat = 452
        static let maxEvidenceHeight: CGFloat = 320
        static let minEvidenceHeight: CGFloat = 80
        static let horizontalInset: CGFloat = 16
        static let verticalInset: CGFloat = 14
    }

    private let snapshot: MeterSnapshot
    private let budget: MonthlyBudget?
    private let quotas: [ProviderQuota]
    private let sessions: [RecentSession]
    private var selectedTab: MenuTab
    private let onTab: (MenuTab) -> Void
    private let onOpenDashboard: () -> Void
    private let onOpenBudget: () -> Void
    private let onRefresh: () -> Void
    private let onSelectSession: (String?) -> Void
    private let onSettings: (NSButton) -> Void
    private weak var bodyHost: NSView?
    private weak var evidenceScroll: NSScrollView?
    private var bodyHeightConstraint: NSLayoutConstraint?
    private var chromeHeight: CGFloat = 0
    private var tabButtons: [NSButton] = []
    private var tabMarkers: [NSView] = []

    init(
        snapshot: MeterSnapshot,
        budget: MonthlyBudget?,
        quotas: [ProviderQuota],
        sessions: [RecentSession],
        selectedTab: MenuTab,
        onTab: @escaping (MenuTab) -> Void,
        onOpenDashboard: @escaping () -> Void,
        onOpenBudget: @escaping () -> Void,
        onRefresh: @escaping () -> Void,
        onSelectSession: @escaping (String?) -> Void,
        onSettings: @escaping (NSButton) -> Void
    ) {
        self.snapshot = snapshot
        self.budget = budget
        self.quotas = quotas
        self.sessions = sessions
        self.selectedTab = selectedTab
        self.onTab = onTab
        self.onOpenDashboard = onOpenDashboard
        self.onOpenBudget = onOpenBudget
        self.onRefresh = onRefresh
        self.onSelectSession = onSelectSession
        self.onSettings = onSettings
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { nil }

    override func loadView() {
        let root = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: Layout.width, height: Layout.maxHeight))
        root.material = .hudWindow
        root.blendingMode = .withinWindow
        root.state = .active
        root.appearance = NSAppearance(named: .darkAqua)
        root.wantsLayer = true
        root.layer?.backgroundColor = NSColor.tokenMeterInk.cgColor
        root.layer?.cornerRadius = 14
        view = root
        root.widthAnchor.constraint(equalToConstant: Layout.width).isActive = true

        let column = NSStackView()
        column.orientation = .vertical
        column.alignment = .leading
        column.distribution = .fill
        column.spacing = 11
        column.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: Layout.horizontalInset),
            column.widthAnchor.constraint(equalToConstant: Layout.width - Layout.horizontalInset * 2),
            column.topAnchor.constraint(equalTo: root.topAnchor, constant: Layout.verticalInset),
            column.bottomAnchor.constraint(lessThanOrEqualTo: root.bottomAnchor, constant: -Layout.verticalInset),
        ])

        column.addArrangedSubview(makeHeader())
        column.addArrangedSubview(makeTabs())
        let evidenceBody = selectedTab == .run ? makeRunBody() : makeQuotaBody()
        let evidence = makeScrollableEvidenceBody(evidenceBody)
        let bodyHost = NSView()
        bodyHost.wantsLayer = true
        bodyHost.layer?.masksToBounds = true
        bodyHost.translatesAutoresizingMaskIntoConstraints = false
        let bodyHeightConstraint = bodyHost.heightAnchor.constraint(equalToConstant: evidence.height)
        bodyHeightConstraint.isActive = true
        installEvidenceScroll(evidence.scroll, in: bodyHost)
        self.bodyHost = bodyHost
        self.evidenceScroll = evidence.scroll
        self.bodyHeightConstraint = bodyHeightConstraint
        column.addArrangedSubview(bodyHost)
        column.addArrangedSubview(makeFooter())
        for child in column.arrangedSubviews {
            child.widthAnchor.constraint(equalTo: column.widthAnchor).isActive = true
        }

        root.layoutSubtreeIfNeeded()
        let fittedHeight = min(Layout.maxHeight, ceil(column.fittingSize.height + Layout.verticalInset * 2))
        chromeHeight = fittedHeight - evidence.height
        root.frame.size = NSSize(width: Layout.width, height: fittedHeight)
        preferredContentSize = NSSize(width: Layout.width, height: fittedHeight)
    }

    private func makeHeader() -> NSView {
        let row = horizontal(spacing: 8)
        if let wordmark = splunkWordmarkImage() {
            let logo = NSImageView(image: wordmark)
            logo.imageScaling = .scaleProportionallyUpOrDown
            logo.translatesAutoresizingMaskIntoConstraints = false
            logo.widthAnchor.constraint(equalToConstant: 63).isActive = true
            logo.heightAnchor.constraint(equalToConstant: 25).isActive = true
            logo.setContentHuggingPriority(.required, for: .horizontal)
            logo.setAccessibilityLabel("Splunk")
            row.addArrangedSubview(logo)
        } else {
            let logo = NSImageView(image: splunkChevronImage())
            logo.contentTintColor = .tokenMeterBlue
            logo.setContentHuggingPriority(.required, for: .horizontal)
            row.addArrangedSubview(logo)
        }
        row.addArrangedSubview(label("Token Meter", size: 14, weight: .semibold, color: .labelColor))
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(spacer)
        let live = label(snapshot.connected ? (snapshot.ended ? "PINNED" : "LIVE") : "OFFLINE", size: 10.5, weight: .bold,
                         color: snapshot.connected ? .tokenMeterBlue : .systemOrange)
        live.setContentHuggingPriority(.required, for: .horizontal)
        row.addArrangedSubview(live)
        return row
    }

    private func makeTabs() -> NSView {
        let rail = horizontal(spacing: 0)
        rail.distribution = .fillEqually
        rail.heightAnchor.constraint(equalToConstant: 29).isActive = true
        for (index, tab) in MenuTab.allCases.enumerated() {
            let item = vertical(spacing: 0)
            item.alignment = .centerX
            let button = NSButton(title: tab.title, target: self, action: #selector(selectTab(_:)))
            button.tag = index
            button.bezelStyle = .inline
            button.isBordered = false
            button.font = .systemFont(ofSize: 11.5, weight: tab == selectedTab ? .semibold : .medium)
            button.contentTintColor = tab == selectedTab ? .tokenMeterBlue : .tokenMeterMuted
            button.alignment = .center
            button.focusRingType = .none
            button.setAccessibilityLabel("Show \(tab.title)")
            item.addArrangedSubview(button)
            button.widthAnchor.constraint(equalTo: item.widthAnchor).isActive = true
            button.heightAnchor.constraint(equalToConstant: 27).isActive = true
            let marker = NSView()
            marker.wantsLayer = true
            marker.layer?.backgroundColor = (tab == selectedTab ? NSColor.tokenMeterBlue : NSColor.clear).cgColor
            marker.widthAnchor.constraint(equalToConstant: 28).isActive = true
            marker.heightAnchor.constraint(equalToConstant: 2).isActive = true
            item.addArrangedSubview(marker)
            rail.addArrangedSubview(item)
            tabButtons.append(button)
            tabMarkers.append(marker)
        }
        return rail
    }

    func transition(to tab: MenuTab) -> NSSize {
        _ = view
        guard tab != selectedTab,
              let host = bodyHost,
              let heightConstraint = bodyHeightConstraint
        else { return preferredContentSize }

        view.layoutSubtreeIfNeeded()
        let outgoing = evidenceScroll
        for staleView in host.subviews where staleView !== outgoing {
            staleView.removeFromSuperview()
        }

        selectedTab = tab
        updateTabAppearance()
        let body = selectedTab == .run ? makeRunBody() : makeQuotaBody()
        let incoming = makeScrollableEvidenceBody(body)
        installEvidenceScroll(incoming.scroll, in: host)
        evidenceScroll = incoming.scroll
        heightConstraint.constant = incoming.height
        let targetSize = NSSize(
            width: Layout.width,
            height: min(Layout.maxHeight, ceil(chromeHeight + incoming.height))
        )
        outgoing?.removeFromSuperview()
        incoming.scroll.alphaValue = 1
        view.layoutSubtreeIfNeeded()
        return targetSize
    }

    func settlePreferredContentSize(_ size: NSSize) {
        preferredContentSize = size
    }

    func tabsAreFullyVisible() -> Bool {
        view.layoutSubtreeIfNeeded()
        guard tabButtons.count == MenuTab.allCases.count else { return false }
        return tabButtons.allSatisfy { button in
            guard !button.isHidden, button.window === view.window else { return false }
            let buttonFrame = view.convert(button.bounds, from: button)
            return view.visibleRect.contains(buttonFrame)
                && button.frame.width >= 70
                && button.frame.height >= 24
        }
    }

    func tabVisibilitySummary() -> String {
        view.layoutSubtreeIfNeeded()
        let frames = tabButtons.map { button -> String in
            let frame = view.convert(button.bounds, from: button)
            return "\(button.title):\(Int(frame.minX)),\(Int(frame.minY)),\(Int(frame.width))x\(Int(frame.height)),window=\(button.window === view.window)"
        }.joined(separator: ";")
        return "bounds=\(Int(view.bounds.width))x\(Int(view.bounds.height)) visible=\(Int(view.visibleRect.width))x\(Int(view.visibleRect.height)) \(frames)"
    }

    private func updateTabAppearance() {
        for (index, tab) in MenuTab.allCases.enumerated() where tabButtons.indices.contains(index) {
            let selected = tab == selectedTab
            tabButtons[index].font = .systemFont(ofSize: 11.5, weight: selected ? .semibold : .medium)
            tabButtons[index].contentTintColor = selected ? .tokenMeterBlue : .tokenMeterMuted
            tabButtons[index].setAccessibilityValue(selected ? "Selected" : "Not selected")
            if tabMarkers.indices.contains(index) {
                tabMarkers[index].layer?.backgroundColor = (selected ? NSColor.tokenMeterBlue : NSColor.clear).cgColor
            }
        }
    }

    private func makeScrollableEvidenceBody(_ content: NSView) -> (scroll: NSScrollView, height: CGFloat) {
        content.translatesAutoresizingMaskIntoConstraints = false
        let measurementWidth = content.widthAnchor.constraint(
            equalToConstant: Layout.width - Layout.horizontalInset * 2
        )
        measurementWidth.isActive = true
        content.layoutSubtreeIfNeeded()
        let measuredHeight = ceil(content.fittingSize.height)
        measurementWidth.isActive = false
        let viewportHeight = min(
            Layout.maxEvidenceHeight,
            max(Layout.minEvidenceHeight, measuredHeight)
        )

        let scroll = NSScrollView()
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.contentView.drawsBackground = false
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)
        scroll.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        scroll.documentView = content
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            content.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            content.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
        ])
        return (scroll, viewportHeight)
    }

    private func installEvidenceScroll(_ scroll: NSScrollView, in host: NSView) {
        scroll.translatesAutoresizingMaskIntoConstraints = false
        host.addSubview(scroll)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: host.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: host.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: host.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: host.bottomAnchor),
        ])
    }

    private func makeRunBody() -> NSView {
        guard snapshot.connected else {
            let offline = vertical(spacing: 8)
            offline.addArrangedSubview(label("Token Meter is not reachable", size: 15, weight: .semibold, color: .labelColor))
            offline.addArrangedSubview(label(snapshot.error, size: 12, weight: .regular, color: .tokenMeterMuted, lines: 2))
            let action = textButton("Open dashboard", action: #selector(openDashboard(_:)), primary: true)
            offline.addArrangedSubview(action)
            return offline
        }

        let body = vertical(spacing: 10)
        let identity = horizontal(spacing: 7)
        let providerIcon = NSImageView(image: NSImage(systemSymbolName: providerSymbol(snapshot.provider), accessibilityDescription: snapshot.provider) ?? NSImage())
        providerIcon.contentTintColor = .tokenMeterBlue
        identity.addArrangedSubview(providerIcon)
        let text = vertical(spacing: 1)
        let selectedName = sessions.first { $0.id == snapshot.selectedSessionID }?.identifier ?? snapshot.menuTitle
        let selectedTitle = label(selectedName, size: 13.5, weight: .semibold, color: .labelColor)
        selectedTitle.toolTip = selectedName
        selectedTitle.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        text.addArrangedSubview(selectedTitle)
        text.addArrangedSubview(label("\(snapshot.provider) · \(snapshot.model)", size: 11, weight: .medium, color: .tokenMeterMuted))
        identity.addArrangedSubview(text)
        body.addArrangedSubview(identity)
        identity.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true

        let ledger = makeMetricLedger()
        body.addArrangedSubview(ledger)
        ledger.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true

        let pressure = vertical(spacing: 3)
        let pressureHeader = horizontal(spacing: 8)
        pressureHeader.addArrangedSubview(label("Context pressure", size: 11, weight: .semibold, color: .tokenMeterMuted))
        let pressureSpacer = NSView()
        pressureSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        pressureHeader.addArrangedSubview(pressureSpacer)
        let pct = snapshot.contextPct.map { "\(Int(($0 * 100).rounded()))%" } ?? "--"
        pressureHeader.addArrangedSubview(label(pct, size: 19, weight: .bold, color: contextSignalColor(snapshot.contextPct)))
        let measured = snapshot.contextPct == nil
            ? "Not reported by this trace"
            : "\(formatCompactInt(snapshot.contextTokens)) / \(formatCompactInt(snapshot.contextWindow)) measured"
        pressure.addArrangedSubview(pressureHeader)
        pressureHeader.widthAnchor.constraint(equalTo: pressure.widthAnchor).isActive = true
        pressure.addArrangedSubview(label(measured, size: 10.5, weight: .medium, color: .tokenMeterMuted))
        let trace = ExecutionTraceView(values: snapshot.contextPulse)
        pressure.addArrangedSubview(trace)
        trace.widthAnchor.constraint(equalTo: pressure.widthAnchor).isActive = true
        body.addArrangedSubview(pressure)
        pressure.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true

        let signalDivider = divider()
        body.addArrangedSubview(signalDivider)
        signalDivider.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
        if let budget = budget, budget.configured {
            let budgetLine = makeBudgetLine(budget)
            body.addArrangedSubview(budgetLine)
            budgetLine.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
        }

        let result = vertical(spacing: 9)
        result.addArrangedSubview(body)
        body.widthAnchor.constraint(equalTo: result.widthAnchor).isActive = true
        if !sessions.isEmpty {
            let sessionsDivider = divider()
            result.addArrangedSubview(sessionsDivider)
            sessionsDivider.widthAnchor.constraint(equalTo: result.widthAnchor).isActive = true
            let sessions = makeSessions()
            result.addArrangedSubview(sessions)
            sessions.widthAnchor.constraint(equalTo: result.widthAnchor).isActive = true
        }
        return result
    }

    private func makeMetricLedger() -> NSView {
        let ledger = horizontal(spacing: 10)
        let cost = metricColumn("Run cost", snapshot.costLabel, tooltip: snapshot.pricingNote)
        ledger.addArrangedSubview(cost)
        ledger.addArrangedSubview(divider(vertical: true))
        let output = metricColumn("Output pace", snapshot.outputSpeedLabel, tooltip: snapshot.outputSpeedTooltip)
        ledger.addArrangedSubview(output)
        ledger.addArrangedSubview(divider(vertical: true))
        let latest = snapshot.costAvailable ? formatMoney(snapshot.lastTurnCost) : "--"
        let latestLabel = snapshot.estimatedCost ? "Latest est." : "Latest"
        let latestColumn = metricColumn(latestLabel, latest)
        ledger.addArrangedSubview(latestColumn)
        cost.widthAnchor.constraint(equalTo: output.widthAnchor).isActive = true
        output.widthAnchor.constraint(equalTo: latestColumn.widthAnchor).isActive = true
        return ledger
    }

    private func makeBudgetLine(_ budget: MonthlyBudget) -> NSView {
        let row = horizontal(spacing: 8)
        let icon = NSImageView(image: NSImage(systemSymbolName: budget.anyExceeded ? "exclamationmark.triangle.fill" : "calendar", accessibilityDescription: "Monthly budget") ?? NSImage())
        icon.contentTintColor = budget.anyExceeded ? .systemOrange : .tokenMeterBlue
        row.addArrangedSubview(icon)
        row.addArrangedSubview(label("Monthly budget", size: 11.5, weight: .semibold, color: .tokenMeterMuted))
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(spacer)
        let action = textButton(budget.compactLabel, action: #selector(openBudget(_:)), primary: false)
        action.setContentHuggingPriority(.required, for: .horizontal)
        action.toolTip = budget.toolTip
        row.addArrangedSubview(action)
        return row
    }

    private func makeSessions() -> NSView {
        let stack = vertical(spacing: 1)
        let heading = horizontal(spacing: 7)
        heading.addArrangedSubview(label("Recent sessions", size: 11.5, weight: .semibold, color: .tokenMeterMuted))
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        heading.addArrangedSubview(spacer)
        if snapshot.pinnedSession {
            let follow = textButton("Follow latest", action: #selector(followLatest(_:)), primary: true)
            follow.setContentHuggingPriority(.required, for: .horizontal)
            follow.toolTip = "Stop viewing this pinned session and follow the latest session"
            heading.addArrangedSubview(follow)
        }
        stack.addArrangedSubview(heading)
        heading.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        for (index, session) in sessions.prefix(3).enumerated() {
            let row = horizontal(spacing: 7)
            row.heightAnchor.constraint(greaterThanOrEqualToConstant: 21).isActive = true
            let icon = NSImageView(image: NSImage(systemSymbolName: session.symbolName, accessibilityDescription: session.providerName) ?? NSImage())
            icon.contentTintColor = session.id == snapshot.selectedSessionID ? .tokenMeterBlue : .tokenMeterMuted
            icon.widthAnchor.constraint(equalToConstant: 14).isActive = true
            row.addArrangedSubview(icon)
            let item = textButton(session.identifier, action: #selector(selectSession(_:)), primary: false)
            item.tag = index
            item.alignment = .left
            item.contentTintColor = session.id == snapshot.selectedSessionID ? .tokenMeterBlue : .labelColor
            item.cell?.lineBreakMode = .byTruncatingTail
            item.setContentHuggingPriority(.defaultLow, for: .horizontal)
            item.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            item.toolTip = "Switch to \(session.toolTip)"
            row.addArrangedSubview(item)
            let provider = label(session.providerName, size: 10.5, weight: .medium, color: .tokenMeterMuted)
            provider.setContentHuggingPriority(.required, for: .horizontal)
            row.addArrangedSubview(provider)
            stack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        return stack
    }

    private func makeQuotaBody() -> NSView {
        let body = vertical(spacing: 10)
        let scoped = quotas.filter { $0.id == selectedTab.rawValue }
        let title = "\(selectedTab.title) limits"
        body.addArrangedSubview(label(title, size: 16, weight: .semibold, color: .labelColor))

        if scoped.isEmpty {
            body.addArrangedSubview(label("No provider-reported limit is available yet.", size: 12, weight: .regular, color: .tokenMeterMuted, lines: 2))
        } else {
            for provider in scoped {
                let instrument = makeProviderInstrument(provider)
                body.addArrangedSubview(instrument)
                instrument.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
            }
        }
        body.addArrangedSubview(label("Only provider-reported limits are shown.", size: 10.5, weight: .medium, color: .tokenMeterMuted))
        return body
    }

    private func makeProviderInstrument(_ provider: ProviderQuota) -> NSView {
        let stack = vertical(spacing: 6)
        let head = horizontal(spacing: 7)
        head.addArrangedSubview(label(provider.label, size: 13, weight: .semibold, color: .labelColor))
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        head.addArrangedSubview(spacer)
        head.addArrangedSubview(label(provider.freshnessLabel, size: 10.5, weight: .medium, color: provider.stale ? .systemOrange : .tokenMeterMuted))
        stack.addArrangedSubview(head)
        head.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        if provider.windows.isEmpty {
            let message = provider.error.isEmpty ? "No provider-reported quota window is available." : provider.error
            stack.addArrangedSubview(label(message, size: 11.5, weight: .regular, color: .tokenMeterMuted, lines: 2))
        } else {
            for window in provider.windows {
                let row = vertical(spacing: 3)
                let labels = horizontal(spacing: 6)
                labels.addArrangedSubview(label(window.label, size: 11.5, weight: .semibold, color: .labelColor))
                let fill = NSView()
                fill.setContentHuggingPriority(.defaultLow, for: .horizontal)
                labels.addArrangedSubview(fill)
                labels.addArrangedSubview(label(window.percentLabel, size: 11.5, weight: .bold,
                                                color: provider.stale ? .tokenMeterMuted : contextSignalColor(window.usedPercent / 100)))
                row.addArrangedSubview(labels)
                labels.widthAnchor.constraint(equalTo: row.widthAnchor).isActive = true
                let level = LevelBarView(value: window.usedPercent / 100, color: provider.stale ? .tokenMeterMuted : contextSignalColor(window.usedPercent / 100))
                row.addArrangedSubview(level)
                level.widthAnchor.constraint(equalTo: row.widthAnchor).isActive = true
                let detail = [window.resetLabel, window.pace?.summary].compactMap { $0 }.joined(separator: " · ")
                row.addArrangedSubview(label(detail, size: 10.5, weight: .regular, color: .tokenMeterMuted))
                stack.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
            }
        }
        if !provider.coverageNote.isEmpty {
            stack.addArrangedSubview(label(provider.coverageNote, size: 10.5, weight: .regular, color: .tokenMeterMuted, lines: 2))
        }
        return stack
    }

    private func makeFooter() -> NSView {
        let row = horizontal(spacing: 6)
        let open = textButton("Open Token Meter", action: #selector(openDashboard(_:)), primary: true)
        open.image = NSImage(systemSymbolName: "arrow.up.forward.app", accessibilityDescription: "Open Token Meter")
        open.imagePosition = .imageLeading
        row.addArrangedSubview(open)
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(spacer)
        row.addArrangedSubview(symbolButton("arrow.clockwise", label: "Refresh Token Meter", action: #selector(refresh(_:))))
        let settings = textButton("Settings", action: #selector(showSettings(_:)), primary: false)
        settings.image = NSImage(systemSymbolName: "gearshape", accessibilityDescription: "Settings")
        settings.imagePosition = .imageLeading
        settings.toolTip = "Open menu bar settings"
        row.addArrangedSubview(settings)
        return row
    }

    private func metricColumn(_ name: String, _ value: String, tooltip: String? = nil) -> NSView {
        let stack = vertical(spacing: 2)
        stack.alignment = .leading
        stack.addArrangedSubview(label(name, size: 10.5, weight: .semibold, color: .tokenMeterMuted))
        let valueLabel = label(value, size: 14, weight: .bold, color: .labelColor)
        valueLabel.toolTip = tooltip
        stack.addArrangedSubview(valueLabel)
        stack.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return stack
    }

    private func label(_ text: String, size: CGFloat, weight: NSFont.Weight, color: NSColor, lines: Int = 1) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.font = .systemFont(ofSize: size, weight: weight)
        field.textColor = color
        field.lineBreakMode = lines == 1 ? .byTruncatingTail : .byWordWrapping
        field.maximumNumberOfLines = lines
        field.usesSingleLineMode = lines == 1
        return field
    }

    private func textButton(_ title: String, action: Selector, primary: Bool) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .inline
        button.isBordered = false
        button.font = .systemFont(ofSize: 11.5, weight: primary ? .semibold : .medium)
        button.contentTintColor = primary ? .tokenMeterBlue : .labelColor
        button.setAccessibilityLabel(title)
        return button
    }

    private func symbolButton(_ symbol: String, label: String, action: Selector) -> NSButton {
        let button = NSButton(image: NSImage(systemSymbolName: symbol, accessibilityDescription: label) ?? NSImage(), target: self, action: action)
        button.bezelStyle = .inline
        button.contentTintColor = .tokenMeterMuted
        button.toolTip = label
        button.setAccessibilityLabel(label)
        return button
    }

    private func horizontal(spacing: CGFloat) -> NSStackView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.distribution = .fill
        stack.spacing = spacing
        return stack
    }

    private func vertical(spacing: CGFloat) -> NSStackView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.distribution = .fill
        stack.spacing = spacing
        return stack
    }

    private func divider(vertical: Bool = false) -> NSView {
        let line = NSView()
        line.wantsLayer = true
        line.layer?.backgroundColor = NSColor.tokenMeterLine.withAlphaComponent(0.74).cgColor
        if vertical {
            line.widthAnchor.constraint(equalToConstant: 1).isActive = true
            line.heightAnchor.constraint(greaterThanOrEqualToConstant: 32).isActive = true
        } else {
            line.heightAnchor.constraint(equalToConstant: 1).isActive = true
        }
        return line
    }

    @objc private func selectTab(_ sender: NSButton) {
        let tabs = MenuTab.allCases
        guard tabs.indices.contains(sender.tag) else { return }
        onTab(tabs[sender.tag])
    }

    @objc private func openDashboard(_ sender: NSButton) { onOpenDashboard() }
    @objc private func openBudget(_ sender: NSButton) { onOpenBudget() }
    @objc private func refresh(_ sender: NSButton) { onRefresh() }
    @objc private func followLatest(_ sender: NSButton) { onSelectSession(nil) }
    @objc private func showSettings(_ sender: NSButton) { onSettings(sender) }

    @objc private func selectSession(_ sender: NSButton) {
        guard sessions.indices.contains(sender.tag) else { return }
        onSelectSession(sessions[sender.tag].id)
    }
}

private func contextSignalColor(_ value: Double?) -> NSColor {
    guard let value = value else { return .tokenMeterMuted }
    if value >= 0.85 { return .systemOrange }
    if value >= 0.70 { return .systemYellow }
    return .tokenMeterBlue
}

private func providerSymbol(_ provider: String) -> String {
    switch provider.lowercased() {
    case "codex": return "terminal"
    case "cursor": return "cursorarrow"
    default: return "sparkles"
    }
}

final class TokenMeterMenuBar: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private let stateURL = tokenMeterMenubarURL
    private let dashboardURL = tokenMeterDashboardURL
    private let popover = NSPopover()
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var pinnedSessionID = tokenMeterDefaults.string(forKey: pinnedSessionDefaultsKey)
    private var recentSessions: [RecentSession] = []
    private var providerQuotas: [ProviderQuota] = []
    private var selectedTab = MenuTab(
        rawValue: tokenMeterDefaults.string(forKey: selectedTabDefaultsKey) ?? ""
    ) ?? .run
    private var statusDisplayMode = StatusDisplayMode(
        rawValue: tokenMeterDefaults.string(forKey: statusDisplayModeDefaultsKey) ?? StatusDisplayMode.text.rawValue
    ) ?? .text
    private var globalShortcut = MenuBarShortcut(
        rawValue: tokenMeterDefaults.string(forKey: globalShortcutDefaultsKey) ?? ""
    ) ?? .controlOptionT
    private var customShortcutKeyCode: UInt32? = {
        guard tokenMeterDefaults.object(forKey: customShortcutKeyCodeDefaultsKey) != nil else { return nil }
        return UInt32(tokenMeterDefaults.integer(forKey: customShortcutKeyCodeDefaultsKey))
    }()
    private var customShortcutModifiers: UInt32 = UInt32(
        tokenMeterDefaults.integer(forKey: customShortcutModifiersDefaultsKey)
    )
    private var globalHotKey: EventHotKeyRef?
    private var globalHotKeyHandler: EventHandlerRef?
    private var titleMetrics: Set<TitleMetric> = {
        if let saved = tokenMeterDefaults.array(forKey: titleMetricsDefaultsKey) as? [String] {
            let metrics = Set(saved.compactMap(TitleMetric.init(rawValue:)))
            if !metrics.isEmpty { return metrics }
        }
        if tokenMeterDefaults.string(forKey: titleModeDefaultsKey) == "limits" {
            return [.limits]
        }
        return [.cost, .speed]
    }()
    private var quotaAlertsEnabled: Bool = {
        if tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil { return true }
        return tokenMeterDefaults.bool(forKey: quotaAlertsEnabledDefaultsKey)
    }()
    private var quotaAlertThreshold: Int = {
        let saved = tokenMeterDefaults.integer(forKey: quotaAlertThresholdDefaultsKey)
        return [80, 90, 95].contains(saved) ? saved : 80
    }()
    private var quotaNotificationStates: [String: QuotaNotificationState] = {
        guard let data = tokenMeterDefaults.data(forKey: quotaNotificationStatesDefaultsKey),
              let states = try? JSONDecoder().decode([String: QuotaNotificationState].self, from: data)
        else { return [:] }
        return states
    }()
    private var budgetNotificationStates: [String: BudgetNotificationState] = {
        guard let data = tokenMeterDefaults.data(forKey: budgetNotificationStatesDefaultsKey),
              let states = try? JSONDecoder().decode([String: BudgetNotificationState].self, from: data)
        else { return [:] }
        return states
    }()
    private var budgetExceededNotificationMonths = Set(
        tokenMeterDefaults.stringArray(forKey: budgetExceededMonthsDefaultsKey) ?? []
    )
    private var quotaObservationEstablished = false
    private var popoverRefreshPending = false
    private var surfaceTransitionGeneration = 0
    private var settledSurfaceTransitionGeneration = 0
    private var snapshot = MeterSnapshot.disconnected("Waiting for http://127.0.0.1:8722/menubar")
    private var monthlyBudget: MonthlyBudget?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if tokenMeterDefaults.object(forKey: statusItemPreferredPositionDefaultsKey) == nil {
            tokenMeterDefaults.set(statusItemInitialPreferredPosition, forKey: statusItemPreferredPositionDefaultsKey)
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.autosaveName = statusItemAutosaveName
        if let button = statusItem.button {
            button.image = splunkChevronImage()
            button.imagePosition = .imageOnly
            button.contentTintColor = .tokenMeterBlue
            button.target = self
            button.action = #selector(togglePopover(_:))
            button.sendAction(on: [.leftMouseUp])
        }
        popover.appearance = NSAppearance(named: .vibrantDark)
        popover.behavior = .transient
        popover.animates = true
        popover.delegate = self
        registerGlobalHotKey()
        refreshSurface()
        fetchState()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.fetchState()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        unregisterGlobalHotKey()
    }

    func popoverDidClose(_ notification: Notification) {
        guard popoverRefreshPending else { return }
        popoverRefreshPending = false
        refreshSurface(force: true)
    }

    private func fetchState(
        forceSurfaceRefresh: Bool = false,
        animatedSurfaceRefresh: Bool = false
    ) {
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
                    self.refreshSurface(force: forceSurfaceRefresh, animated: animatedSurfaceRefresh)
                    return
                }
                guard
                    let data = data,
                    let obj = try? JSONSerialization.jsonObject(with: data),
                    let dict = obj as? [String: Any]
                else {
                    self.snapshot = MeterSnapshot.disconnected("Token Meter returned unreadable state.")
                    self.refreshSurface(force: forceSurfaceRefresh, animated: animatedSurfaceRefresh)
                    return
                }
                let selection = dict["selection"] as? [String: Any] ?? [:]
                if bool(selection["missing"]) {
                    self.persistPinnedSession(nil)
                }
                self.recentSessions = (dict["recent_sessions"] as? [[String: Any]] ?? [])
                    .compactMap(RecentSession.fromJSON)
                self.providerQuotas = (dict["provider_quotas"] as? [[String: Any]] ?? [])
                    .compactMap(ProviderQuota.fromJSON)
                self.snapshot = MeterSnapshot.fromJSON(dict)
                self.monthlyBudget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
                self.evaluateQuotaNotifications()
                self.evaluateBudgetNotifications()
                self.refreshSurface(force: forceSurfaceRefresh, animated: animatedSurfaceRefresh)
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
        refreshSurface()
    }

    private func refreshSurface(force: Bool = false, animated: Bool = false) {
        updateStatusTitle()
        guard force || !popover.isShown else {
            popoverRefreshPending = true
            return
        }
        let controller = RunPulsePopoverController(
            snapshot: snapshot,
            budget: monthlyBudget,
            quotas: providerQuotas,
            sessions: recentSessions,
            selectedTab: selectedTab,
            onTab: { [weak self] tab in self?.changeTab(tab) },
            onOpenDashboard: { [weak self] in self?.openDashboard() },
            onOpenBudget: { [weak self] in self?.openBudgetSettings() },
            onRefresh: { [weak self] in
                self?.fetchState(forceSurfaceRefresh: true, animatedSurfaceRefresh: true)
            },
            onSelectSession: { [weak self] sessionID in self?.selectPulseSession(sessionID) },
            onSettings: { [weak self] button in self?.showSettingsMenu(from: button) }
        )
        let targetSize = controller.preferredContentSize
        popover.contentViewController = controller
        resizePopover(to: targetSize)
    }

    private func resizePopover(to targetSize: NSSize) {
        surfaceTransitionGeneration += 1
        let transitionGeneration = surfaceTransitionGeneration
        popover.contentSize = targetSize
        (popover.contentViewController as? RunPulsePopoverController)?.settlePreferredContentSize(targetSize)
        settledSurfaceTransitionGeneration = transitionGeneration
        if popover.isShown,
           ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_POPOVER_SMOKE"] == "1" {
            let tabsVisible = (popover.contentViewController as? RunPulsePopoverController)?.tabsAreFullyVisible() ?? false
            print("run-pulse-transition-settle expected=\(transitionGeneration) current=\(surfaceTransitionGeneration) tabs=\(tabsVisible) mode=immediate")
        }
    }

    @objc private func togglePopover(_ sender: Any?) {
        if popover.isShown {
            popover.performClose(sender)
            return
        }
        refreshSurface(force: true)
        guard let button = statusItem.button else { return }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    private var activeShortcutKeyCode: UInt32? {
        globalShortcut == .custom ? customShortcutKeyCode : globalShortcut.keyCode
    }

    private var activeShortcutModifiers: UInt32 {
        globalShortcut == .custom ? customShortcutModifiers : globalShortcut.modifiers
    }

    private func registerGlobalHotKey() {
        unregisterGlobalHotKey()
        guard let keyCode = activeShortcutKeyCode else { return }

        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, userData in
                guard let userData = userData else { return noErr }
                let delegate = Unmanaged<TokenMeterMenuBar>.fromOpaque(userData).takeUnretainedValue()
                DispatchQueue.main.async {
                    delegate.togglePopover(nil)
                }
                return noErr
            },
            1,
            &eventSpec,
            userData,
            &globalHotKeyHandler
        )
        guard handlerStatus == noErr else { return }

        let hotKeyID = EventHotKeyID(signature: 0x544D4854, id: 1)
        let registrationStatus = RegisterEventHotKey(
            keyCode,
            activeShortcutModifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &globalHotKey
        )
        if registrationStatus != noErr {
            unregisterGlobalHotKey()
        }
    }

    private func unregisterGlobalHotKey() {
        if let globalHotKey = globalHotKey {
            UnregisterEventHotKey(globalHotKey)
            self.globalHotKey = nil
        }
        if let globalHotKeyHandler = globalHotKeyHandler {
            RemoveEventHandler(globalHotKeyHandler)
            self.globalHotKeyHandler = nil
        }
    }

    private func changeTab(_ tab: MenuTab) {
        guard tab != selectedTab else { return }
        selectedTab = tab
        tokenMeterDefaults.set(selectedTab.rawValue, forKey: selectedTabDefaultsKey)
        if popover.isShown,
           let controller = popover.contentViewController as? RunPulsePopoverController {
            let targetSize = controller.transition(to: tab)
            resizePopover(to: targetSize)
        } else {
            refreshSurface(force: true)
        }
    }

    private func selectPulseSession(_ sessionID: String?) {
        persistPinnedSession(sessionID)
        fetchState(forceSurfaceRefresh: true, animatedSurfaceRefresh: true)
    }

    func runPopoverSmoke() throws {
        let originalTab = selectedTab
        let originalPersistedTab = tokenMeterDefaults.string(forKey: selectedTabDefaultsKey)
        defer {
            selectedTab = originalTab
            if let originalPersistedTab = originalPersistedTab {
                tokenMeterDefaults.set(originalPersistedTab, forKey: selectedTabDefaultsKey)
            } else {
                tokenMeterDefaults.removeObject(forKey: selectedTabDefaultsKey)
            }
            timer?.invalidate()
            popover.performClose(nil)
            statusItem = nil
        }
        let smokeData = try Data(contentsOf: stateURL)
        let smokeObject = try JSONSerialization.jsonObject(with: smokeData)
        guard let smokePayload = smokeObject as? [String: Any] else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 12,
                userInfo: [NSLocalizedDescriptionKey: "Run Pulse smoke payload was not a JSON object."]
            )
        }
        snapshot = MeterSnapshot.fromJSON(smokePayload)
        monthlyBudget = MonthlyBudget.fromJSON(smokePayload["budget"] as? [String: Any])
        providerQuotas = (smokePayload["provider_quotas"] as? [[String: Any]] ?? [])
            .compactMap(ProviderQuota.fromJSON)
        recentSessions = (smokePayload["recent_sessions"] as? [[String: Any]] ?? [])
            .compactMap(RecentSession.fromJSON)
        togglePopover(nil)
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.25))
        guard popover.isShown, popover.contentViewController is RunPulsePopoverController else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "Status-item click did not show Run Pulse."]
            )
        }
        guard let mountedController = popover.contentViewController as? RunPulsePopoverController,
              mountedController.tabsAreFullyVisible()
        else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 10,
                userInfo: [NSLocalizedDescriptionKey: "Run Pulse did not show all four scope tabs before switching."]
            )
        }
        let transitionGeneration = surfaceTransitionGeneration
        changeTab(.claude)
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.08))
        guard popover.contentViewController === mountedController,
              mountedController.tabsAreFullyVisible() else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 11,
                userInfo: [NSLocalizedDescriptionKey: "Run Pulse clipped a scope tab after switching to Claude (\(mountedController.tabVisibilitySummary()))."]
            )
        }
        changeTab(.cursor)
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.08))
        guard popover.contentViewController === mountedController,
              mountedController.tabsAreFullyVisible(),
              surfaceTransitionGeneration > transitionGeneration,
              settledSurfaceTransitionGeneration == surfaceTransitionGeneration,
              popover.contentSize == popover.contentViewController?.preferredContentSize
        else {
            let actualSize = popover.contentSize
            let expectedSize = popover.contentViewController?.preferredContentSize ?? .zero
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 5,
                userInfo: [NSLocalizedDescriptionKey: "Run Pulse transition did not complete (tabs \(mountedController.tabsAreFullyVisible()), \(mountedController.tabVisibilitySummary()), generation \(surfaceTransitionGeneration), settled \(settledSurfaceTransitionGeneration), size \(actualSize), expected \(expectedSize))."]
            )
        }
    }

    private func updateStatusTitle() {
        let title = compactStatusTitle()
        let budgetExceeded = monthlyBudget?.anyExceeded == true
        guard let button = statusItem.button else { return }
        button.contentTintColor = budgetExceeded
            ? NSColor.systemRed
            : (snapshot.connected ? NSColor.tokenMeterBlue : NSColor.secondaryLabelColor)
        switch effectiveStatusDisplayMode() {
        case .text:
            let titleImage = statusTitleImage(title)
            statusItem.length = titleImage.size.width + 16
            button.title = ""
            button.attributedTitle = NSAttributedString(string: "")
            button.image = titleImage
            button.imagePosition = .imageOnly
            button.contentTintColor = nil
        case .icon:
            statusItem.length = NSStatusItem.squareLength
            button.title = ""
            button.attributedTitle = NSAttributedString(string: "")
            button.image = splunkChevronImage()
            button.imagePosition = .imageOnly
        case .automatic:
            break
        }
        button.setAccessibilityLabel("Token Meter")
        button.setAccessibilityValue(title)
        var toolTip = "Token Meter · \(title)\n\(snapshot.statusTooltip)"
        if let limits = limitsStatusTooltip() { toolTip += "\nLimits: \(limits)" }
        if let budget = monthlyBudget { toolTip += "\nBudget: \(budget.toolTip)" }
        button.toolTip = toolTip
    }

    private func effectiveStatusDisplayMode() -> StatusDisplayMode {
        guard statusDisplayMode == .automatic else { return statusDisplayMode }
        let screen = statusItem.button?.window?.screen ?? NSScreen.main
        // macOS keeps right-side status items in the usable menu-bar region;
        // a notch alone is not a reason to hide the useful readout. Fall
        // back to the chevron only on genuinely narrow menu bars.
        guard let screen = screen, screen.visibleFrame.width > 0 else { return .text }
        return screen.visibleFrame.width < 1200 ? .icon : .text
    }

    private func compactStatusTitle() -> String {
        guard snapshot.connected else { return snapshot.verdict.prefix }
        let base = "\(snapshot.costLabel) · \(snapshot.outputSpeedLabel)"
        return monthlyBudget?.anyExceeded == true ? "⚠︎ \(base)" : base
    }

    private func selectedStatusTitle() -> String {
        guard snapshot.connected else { return snapshot.verdict.prefix }
        let parts = TitleMetric.allCases.compactMap { metric -> String? in
            guard titleMetrics.contains(metric) else { return nil }
            switch metric {
            case .cost: return snapshot.costLabel
            case .speed: return snapshot.outputSpeedLabel
            case .context: return snapshot.contextLabel
            case .model: return snapshot.model
            case .limits: return limitsStatusTitle()
            }
        }
        let base = parts.isEmpty ? "TM" : parts.joined(separator: " · ")
        return monthlyBudget?.anyExceeded == true ? "⚠︎ \(base)" : base
    }

    private func limitsStatusTitle() -> String? {
        guard let constrained = mostConstrainedQuota() else { return nil }
        return "\(constrained.provider.label) \(constrained.window.percentLabel) · \(constrained.window.compactKind)"
    }

    private func limitsStatusTooltip() -> String? {
        guard let constrained = mostConstrainedQuota() else { return nil }
        return "\(constrained.provider.label) \(constrained.window.label): \(constrained.window.percentLabel) used; \(constrained.window.resetLabel)."
    }

    private func mostConstrainedQuota() -> (provider: ProviderQuota, window: QuotaWindow)? {
        providerQuotas
            .filter(\.fresh)
            .flatMap { provider in provider.windows.map { (provider: provider, window: $0) } }
            .max { $0.window.usedPercent < $1.window.usedPercent }
    }

    private func showSettingsMenu(from button: NSButton) {
        let settingsMenu = makeSettingsMenu()
        settingsMenu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.height), in: button)
    }

    private func makeSettingsMenu() -> NSMenu {
        let settingsMenu = NSMenu(title: "Settings")

        let openDashboard = NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "")
        openDashboard.target = self
        settingsMenu.addItem(openDashboard)
        let dailyBrief = NSMenuItem(title: "Open Daily Brief", action: #selector(openDailyBrief), keyEquivalent: "")
        dailyBrief.target = self
        settingsMenu.addItem(dailyBrief)
        let tools = NSMenuItem(title: "Open Tools", action: #selector(openToolsAndSkills), keyEquivalent: "")
        tools.target = self
        settingsMenu.addItem(tools)
        let trace = NSMenuItem(title: "Open Trace", action: #selector(openTrace), keyEquivalent: "")
        trace.target = self
        trace.isEnabled = snapshot.connected
        settingsMenu.addItem(trace)
        settingsMenu.addItem(.separator())
        let openSettings = NSMenuItem(title: "Open Settings", action: #selector(openSettings), keyEquivalent: "")
        openSettings.target = self
        settingsMenu.addItem(openSettings)
        let modelPrices = NSMenuItem(title: "Model Prices", action: #selector(openModelPrices), keyEquivalent: "")
        modelPrices.target = self
        settingsMenu.addItem(modelPrices)
        settingsMenu.addItem(.separator())

        let displayItem = NSMenuItem(title: "Menu bar display", action: nil, keyEquivalent: "")
        let displayMenu = NSMenu(title: "Menu bar display")
        for mode in StatusDisplayMode.allCases {
            let item = NSMenuItem(title: mode.title, action: #selector(setStatusDisplayMode(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = mode.rawValue
            item.state = statusDisplayMode == mode ? .on : .off
            displayMenu.addItem(item)
        }
        displayItem.submenu = displayMenu
        settingsMenu.addItem(displayItem)

        let shortcutItem = NSMenuItem(title: "Keyboard shortcut", action: nil, keyEquivalent: "")
        let shortcutMenu = NSMenu(title: "Keyboard shortcut")
        for shortcut in MenuBarShortcut.allCases {
            let itemTitle = shortcut == .custom ? customShortcutMenuTitle() : shortcut.title
            let action = shortcut == .custom
                ? #selector(configureCustomShortcut(_:))
                : #selector(setGlobalShortcut(_:))
            let item = NSMenuItem(title: itemTitle, action: action, keyEquivalent: "")
            item.target = self
            item.representedObject = shortcut.rawValue
            item.state = shortcut == .custom && customShortcutKeyCode == nil
                ? .off
                : (globalShortcut == shortcut ? .on : .off)
            shortcutMenu.addItem(item)
        }
        shortcutItem.submenu = shortcutMenu
        settingsMenu.addItem(shortcutItem)

        let titleItem = NSMenuItem(title: "Menu bar title", action: nil, keyEquivalent: "")
        let titleMenu = NSMenu(title: "Menu bar title")
        for metric in TitleMetric.allCases {
            let item = NSMenuItem(title: metric.title, action: #selector(toggleTitleMetric(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = metric.rawValue
            item.state = titleMetrics.contains(metric) ? .on : .off
            titleMenu.addItem(item)
        }
        titleItem.submenu = titleMenu
        settingsMenu.addItem(titleItem)

        let alerts = NSMenuItem(title: "Quota notifications", action: #selector(toggleQuotaAlerts(_:)), keyEquivalent: "")
        alerts.target = self
        alerts.state = quotaAlertsEnabled ? .on : .off
        settingsMenu.addItem(alerts)

        let thresholdItem = NSMenuItem(title: "Quota alert threshold (\(quotaAlertThreshold)%)", action: nil, keyEquivalent: "")
        let thresholdMenu = NSMenu(title: "Quota alert threshold")
        for threshold in [80, 90, 95] {
            let item = NSMenuItem(title: "\(threshold)% used", action: #selector(setQuotaThreshold(_:)), keyEquivalent: "")
            item.target = self
            item.tag = threshold
            item.state = quotaAlertThreshold == threshold ? .on : .off
            thresholdMenu.addItem(item)
        }
        thresholdItem.submenu = thresholdMenu
        settingsMenu.addItem(thresholdItem)
        settingsMenu.addItem(.separator())
        let quitItem = NSMenuItem(title: "Quit Token Meter Menubar", action: #selector(quit), keyEquivalent: "")
        quitItem.target = self
        settingsMenu.addItem(quitItem)
        return settingsMenu
    }

    private func persistPinnedSession(_ sessionID: String?) {
        pinnedSessionID = sessionID
        if let sessionID = sessionID {
            tokenMeterDefaults.set(sessionID, forKey: pinnedSessionDefaultsKey)
        } else {
            tokenMeterDefaults.removeObject(forKey: pinnedSessionDefaultsKey)
        }
    }

    @objc private func toggleTitleMetric(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let metric = TitleMetric(rawValue: raw)
        else { return }
        if titleMetrics.contains(metric) {
            titleMetrics.remove(metric)
        } else {
            titleMetrics.insert(metric)
        }
        tokenMeterDefaults.set(
            TitleMetric.allCases.filter(titleMetrics.contains).map(\.rawValue),
            forKey: titleMetricsDefaultsKey
        )
        tokenMeterDefaults.removeObject(forKey: titleModeDefaultsKey)
        refreshMenu()
    }

    @objc private func setStatusDisplayMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let mode = StatusDisplayMode(rawValue: raw)
        else { return }
        statusDisplayMode = mode
        tokenMeterDefaults.set(mode.rawValue, forKey: statusDisplayModeDefaultsKey)
        updateStatusTitle()
        refreshMenu()
    }

    @objc private func setGlobalShortcut(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let shortcut = MenuBarShortcut(rawValue: raw)
        else { return }
        guard shortcut != .custom else {
            configureCustomShortcut(sender)
            return
        }
        globalShortcut = shortcut
        tokenMeterDefaults.set(shortcut.rawValue, forKey: globalShortcutDefaultsKey)
        registerGlobalHotKey()
        refreshMenu()
    }

    private func customShortcutMenuTitle() -> String {
        guard let keyCode = customShortcutKeyCode else { return MenuBarShortcut.custom.title }
        return "Custom (\(shortcutLabel(keyCode: keyCode, modifiers: customShortcutModifiers)))"
    }

    @objc private func configureCustomShortcut(_ sender: NSMenuItem) {
        let alert = NSAlert()
        alert.messageText = "Set Token Meter shortcut"
        alert.informativeText = "Press a key with at least one modifier (⌃, ⌥, ⌘, or ⇧), then choose Save."
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        alert.buttons.first?.isEnabled = false

        var captured: (keyCode: UInt32, modifiers: UInt32)?
        let monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak alert] event in
            let modifiers = carbonModifiers(from: event.modifierFlags)
            guard modifiers != 0 else {
                alert?.informativeText = "Use at least one modifier with the key."
                return nil
            }
            captured = (UInt32(event.keyCode), modifiers)
            alert?.informativeText = "Shortcut: \(shortcutLabel(keyCode: UInt32(event.keyCode), modifiers: modifiers))"
            alert?.buttons.first?.isEnabled = true
            return nil
        }
        let response = alert.runModal()
        if let monitor = monitor { NSEvent.removeMonitor(monitor) }
        guard response == .alertFirstButtonReturn, let captured = captured else { return }

        customShortcutKeyCode = captured.keyCode
        customShortcutModifiers = captured.modifiers
        globalShortcut = .custom
        tokenMeterDefaults.set(MenuBarShortcut.custom.rawValue, forKey: globalShortcutDefaultsKey)
        tokenMeterDefaults.set(Int(captured.keyCode), forKey: customShortcutKeyCodeDefaultsKey)
        tokenMeterDefaults.set(Int(captured.modifiers), forKey: customShortcutModifiersDefaultsKey)
        registerGlobalHotKey()
        refreshMenu()
    }

    @objc private func toggleQuotaAlerts(_ sender: NSMenuItem) {
        quotaAlertsEnabled.toggle()
        tokenMeterDefaults.set(quotaAlertsEnabled, forKey: quotaAlertsEnabledDefaultsKey)
        refreshMenu()
    }

    @objc private func setQuotaThreshold(_ sender: NSMenuItem) {
        guard [80, 90, 95].contains(sender.tag) else { return }
        quotaAlertThreshold = sender.tag
        tokenMeterDefaults.set(sender.tag, forKey: quotaAlertThresholdDefaultsKey)
        refreshMenu()
    }

    private func evaluateQuotaNotifications() {
        var changed = false
        var observedFreshQuota = false
        for provider in providerQuotas where provider.fresh {
            observedFreshQuota = true
            for window in provider.windows {
                let key = "\(provider.id):\(window.id)"
                let resetAt = window.resetAt?.timeIntervalSince1970
                guard var previous = quotaNotificationStates[key] else {
                    quotaNotificationStates[key] = QuotaNotificationState(
                        lastUsedPercent: window.usedPercent,
                        resetAt: resetAt,
                        firedThresholds: []
                    )
                    changed = true
                    continue
                }

                let resetAdvanced: Bool
                if let oldReset = previous.resetAt, let newReset = resetAt {
                    resetAdvanced = newReset > oldReset + 60
                } else {
                    resetAdvanced = false
                }

                if resetAdvanced {
                    if quotaAlertsEnabled, quotaObservationEstablished,
                       previous.lastUsedPercent >= Double(quotaAlertThreshold),
                       window.usedPercent < Double(quotaAlertThreshold) {
                        deliverQuotaNotification(
                            title: "\(provider.label) quota reset",
                            body: "\(window.label) is back to \(window.percentLabel) used."
                        )
                    }
                    previous.firedThresholds.removeAll()
                } else if quotaAlertsEnabled && quotaObservationEstablished {
                    let thresholds = Array(Set([quotaAlertThreshold, 95, 100])).sorted()
                    let crossed = thresholds.filter { threshold in
                        previous.lastUsedPercent < Double(threshold)
                            && window.usedPercent >= Double(threshold)
                            && !previous.firedThresholds.contains(threshold)
                    }
                    if let threshold = crossed.max() {
                        let severity = threshold >= 100 ? "exhausted" : (threshold >= 95 ? "critical" : "warning")
                        deliverQuotaNotification(
                            title: "\(provider.label) quota \(severity)",
                            body: "\(window.label) reached \(window.percentLabel) used; \(window.resetLabel)."
                        )
                        previous.firedThresholds.formUnion(crossed)
                    }
                }

                previous.lastUsedPercent = window.usedPercent
                previous.resetAt = resetAt
                quotaNotificationStates[key] = previous
                changed = true
            }
        }
        if changed, let data = try? JSONEncoder().encode(quotaNotificationStates) {
            tokenMeterDefaults.set(data, forKey: quotaNotificationStatesDefaultsKey)
        }
        if observedFreshQuota {
            quotaObservationEstablished = true
        }
    }

    private func evaluateBudgetNotifications() {
        guard let budget = monthlyBudget, budget.configured else { return }
        if budget.nativeNotifications,
           budget.exceeded,
           !budgetExceededNotificationMonths.contains(budget.month) {
            let prefix = budget.lowerBound ? "At least " : ""
            deliverQuotaNotification(
                title: "Overall monthly budget exceeded",
                body: "\(prefix)\(formatMoney(budget.spend)) of \(formatMoney(budget.budget)) recorded for \(budget.month) (\(Int(budget.percent.rounded()))% used)."
            )
            budgetExceededNotificationMonths.insert(budget.month)
            tokenMeterDefaults.set(
                Array(budgetExceededNotificationMonths).sorted(),
                forKey: budgetExceededMonthsDefaultsKey
            )
        }
        var changed = false
        for scope in budget.scopes {
            let key = scope.id
            guard var previous = budgetNotificationStates[key],
                  previous.month == budget.month
            else {
                budgetNotificationStates[key] = BudgetNotificationState(
                    month: budget.month,
                    lastPercent: scope.percent,
                    firedThresholds: Set(budget.thresholds.filter { scope.percent >= Double($0) })
                )
                changed = true
                continue
            }
            if budget.nativeNotifications {
                let crossed = budget.thresholds.filter { threshold in
                    previous.lastPercent < Double(threshold)
                        && scope.percent >= Double(threshold)
                        && !previous.firedThresholds.contains(threshold)
                }
                let overallExceededAlreadyReported = scope.id == "overall"
                    && budget.exceeded
                    && budgetExceededNotificationMonths.contains(budget.month)
                if let threshold = crossed.max(), !overallExceededAlreadyReported {
                    let prefix = budget.lowerBound ? "At least " : ""
                    deliverQuotaNotification(
                        title: "\(scope.label) monthly budget reached \(threshold)%",
                        body: scope.id == "overall"
                            ? "\(prefix)\(formatMoney(budget.spend)) of \(formatMoney(budget.budget)) recorded for \(budget.month)."
                            : "\(scope.label) reached \(Int(scope.percent.rounded()))% of its allocation."
                    )
                }
                previous.firedThresholds.formUnion(crossed)
            }
            previous.lastPercent = scope.percent
            budgetNotificationStates[key] = previous
            changed = true
        }
        if changed, let data = try? JSONEncoder().encode(budgetNotificationStates) {
            tokenMeterDefaults.set(data, forKey: budgetNotificationStatesDefaultsKey)
        }
    }

    private func deliverQuotaNotification(title: String, body: String) {
        if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = [
            "-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run",
            "--", title, body,
        ]
        try? process.run()
    }

    @objc private func openDashboard() {
        if pinnedSessionID?.isEmpty == false {
            openDashboardPanel("summary")
        } else {
            openDashboardPanel("sessions", includePinnedSession: false)
        }
    }

    @objc private func openDailyBrief() {
        openDashboardPanel("daily", includePinnedSession: false)
    }

    @objc private func openBudgetSettings() {
        NSWorkspace.shared.open(tokenMeterBudgetSettingsURL)
    }

    @objc private func openTrace() {
        openDashboardPanel("activity")
    }

    @objc private func openToolsAndSkills() {
        openDashboardPanel("capabilities", includePinnedSession: false)
    }

    @objc private func openModelPrices() {
        openDashboardPanel("model-pricing", includePinnedSession: false)
    }

    @objc private func openSettings() {
        openDashboardPanel("settings", includePinnedSession: false)
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

private func carbonModifiers(from flags: NSEvent.ModifierFlags) -> UInt32 {
    let flags = flags.intersection(.deviceIndependentFlagsMask)
    var modifiers: UInt32 = 0
    if flags.contains(.control) { modifiers |= UInt32(controlKey) }
    if flags.contains(.option) { modifiers |= UInt32(optionKey) }
    if flags.contains(.command) { modifiers |= UInt32(cmdKey) }
    if flags.contains(.shift) { modifiers |= UInt32(shiftKey) }
    return modifiers
}

private func shortcutLabel(keyCode: UInt32, modifiers: UInt32) -> String {
    var label = ""
    if modifiers & UInt32(controlKey) != 0 { label += "⌃" }
    if modifiers & UInt32(optionKey) != 0 { label += "⌥" }
    if modifiers & UInt32(cmdKey) != 0 { label += "⌘" }
    if modifiers & UInt32(shiftKey) != 0 { label += "⇧" }
    let key = [
        0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V",
        11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T", 31: "O", 32: "U",
        34: "I", 35: "P", 37: "L", 38: "J", 40: "K", 45: "N", 46: "M",
        18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8", 25: "9", 29: "0",
    ][Int(keyCode)] ?? "Key \(keyCode)"
    return label + key
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

private func formatCompactDuration(_ value: TimeInterval) -> String {
    let seconds = max(0, Int(value))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)m" }
    let hours = minutes / 60
    let remainingMinutes = minutes % 60
    if hours < 48 { return "\(hours)h" + (remainingMinutes > 0 ? " \(remainingMinutes)m" : "") }
    let days = hours / 24
    let remainingHours = hours % 24
    return "\(days)d" + (remainingHours > 0 ? " \(remainingHours)h" : "")
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

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_CONTENT_SMOKE"] == "1" {
    _ = NSApplication.shared
    let windows = (0..<8).map { index in
        QuotaWindow(
            id: "window-\(index)",
            kind: index.isMultiple(of: 2) ? "session" : "weekly",
            label: "Measured quota window \(index + 1)",
            usedPercent: Double(20 + index * 9),
            windowSeconds: 3_600,
            resetAt: Date(timeIntervalSinceNow: 3_600),
            pace: QuotaPace(state: "on_pace", summary: "On pace until reset")
        )
    }
    let maximalQuota = ProviderQuota(
        id: "codex",
        label: "Codex",
        status: "ok",
        plan: "Business",
        source: "Provider account",
        provenance: "provider_reported",
        ageSeconds: 0,
        stale: false,
        error: "",
        coverageNote: "Provider-reported maximal payload used to verify bounded native overflow.",
        windows: windows
    )
    let controller = RunPulsePopoverController(
        snapshot: MeterSnapshot.disconnected("Content smoke"), budget: nil, quotas: [maximalQuota], sessions: [], selectedTab: .codex,
        onTab: { _ in }, onOpenDashboard: {}, onOpenBudget: {}, onRefresh: {},
        onSelectSession: { _ in }, onSettings: { _ in }
    )
    let root = controller.view
    root.layoutSubtreeIfNeeded()
    func findEvidenceScroller(in view: NSView) -> NSScrollView? {
        if let scroll = view as? NSScrollView { return scroll }
        return view.subviews.lazy.compactMap(findEvidenceScroller).first
    }
    guard let scroll = findEvidenceScroller(in: root), scroll.hasVerticalScroller else {
        fputs("Token Meter Run Slip content smoke failed: evidence body is not scrollable.\n", stderr)
        exit(1)
    }
    scroll.layoutSubtreeIfNeeded()
    scroll.documentView?.layoutSubtreeIfNeeded()
    let viewportHeight = scroll.contentView.bounds.height
    let documentHeight = scroll.documentView?.fittingSize.height ?? 0
    guard viewportHeight == 320, documentHeight > viewportHeight else {
        fputs("Token Meter Run Slip content smoke failed: maximal payload does not exceed its bounded viewport.\n", stderr)
        exit(1)
    }
    print("run-slip-content=scrollable viewport=\(Int(viewportHeight)) document=\(Int(documentHeight)) windows=\(windows.count)")
    exit(0)
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_LAYOUT_SMOKE"] == "1" {
    do {
        _ = NSApplication.shared
        let data = try Data(contentsOf: tokenMeterMenubarURL)
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw NSError(domain: "TokenMeterMenuBar", code: 2, userInfo: [NSLocalizedDescriptionKey: "Response was not a JSON object."])
        }
        let snapshot = MeterSnapshot.fromJSON(dict)
        let budget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
        let quotas = (dict["provider_quotas"] as? [[String: Any]] ?? []).compactMap(ProviderQuota.fromJSON)
        let sessions = (dict["recent_sessions"] as? [[String: Any]] ?? []).compactMap(RecentSession.fromJSON)
        let controllers = MenuTab.allCases.map { tab in
            RunPulsePopoverController(
                snapshot: snapshot, budget: budget, quotas: quotas, sessions: sessions, selectedTab: tab,
                onTab: { _ in }, onOpenDashboard: {}, onOpenBudget: {}, onRefresh: {},
                onSelectSession: { _ in }, onSettings: { _ in }
            )
        }
        let sizes = controllers.map { controller -> NSSize in
            _ = controller.view
            return controller.preferredContentSize
        }
        guard sizes.allSatisfy({ $0.width == 360 && $0.height <= 452 }) else {
            throw NSError(domain: "TokenMeterMenuBar", code: 3, userInfo: [NSLocalizedDescriptionKey: "Run Pulse layout size is invalid."])
        }
        let scopeTitles = Set(MenuTab.allCases.map(\.title))
        func findScopeButtons(in view: NSView) -> [NSButton] {
            let own = (view as? NSButton).map { scopeTitles.contains($0.title) ? [$0] : [] } ?? []
            return own + view.subviews.flatMap(findScopeButtons)
        }
        for controller in controllers {
            let scopeView = controller.view
            scopeView.layoutSubtreeIfNeeded()
            let buttons = findScopeButtons(in: scopeView)
            guard buttons.count == MenuTab.allCases.count,
                  buttons.allSatisfy({ $0.frame.width >= 70 && $0.frame.height >= 24 })
            else {
                throw NSError(domain: "TokenMeterMenuBar", code: 8, userInfo: [NSLocalizedDescriptionKey: "Scope tabs do not expose full-cell hit targets."])
            }
        }
        let runView = controllers[0].view
        runView.layoutSubtreeIfNeeded()
        func findLayoutScroller(in view: NSView) -> NSScrollView? {
            if let scroll = view as? NSScrollView { return scroll }
            return view.subviews.lazy.compactMap(findLayoutScroller).first
        }
        guard let runScroll = findLayoutScroller(in: runView) else {
            throw NSError(domain: "TokenMeterMenuBar", code: 6, userInfo: [NSLocalizedDescriptionKey: "Run evidence viewport is missing."])
        }
        runScroll.layoutSubtreeIfNeeded()
        runScroll.documentView?.layoutSubtreeIfNeeded()
        let runHeight = runScroll.documentView?.fittingSize.height ?? .infinity
        guard sessions.count < 3 || runHeight <= runScroll.contentView.bounds.height else {
            throw NSError(domain: "TokenMeterMenuBar", code: 7, userInfo: [NSLocalizedDescriptionKey: "Three recent sessions do not fit the Run viewport."])
        }
        let expectedRunViewport = min(320, max(80, runHeight))
        guard abs(runScroll.contentView.bounds.height - expectedRunViewport) < 1 else {
            throw NSError(domain: "TokenMeterMenuBar", code: 9, userInfo: [NSLocalizedDescriptionKey: "Run evidence viewport leaves dead vertical space."])
        }
        let heights = sizes.map { String(Int($0.height)) }.joined(separator: ",")
        print("run-pulse-layout=width-360 heights=\(heights) tabs=\(sizes.count) pulse=\(snapshot.contextPulse.count) run=\(Int(runHeight))")
        exit(0)
    } catch {
        fputs("Token Meter Run Pulse layout smoke failed: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_POPOVER_SMOKE"] == "1" {
    let app = NSApplication.shared
    let delegate = TokenMeterMenuBar()
    app.delegate = delegate
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
        do {
            try delegate.runPopoverSmoke()
            print("run-pulse-popover=shown transition=complete appearance=vibrant-dark")
            exit(0)
        } catch {
            fputs("Token Meter Run Pulse popover smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    app.run()
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" {
    do {
        guard let wordmark = splunkWordmarkImage(),
              wordmark.size.width > 0,
              wordmark.size.height > 0
        else {
            throw NSError(domain: "TokenMeterMenuBar", code: 10, userInfo: [NSLocalizedDescriptionKey: "Bundled Splunk wordmark is unavailable."])
        }
        let chevron = splunkChevronImage()
        guard chevron.isTemplate, chevron.size == NSSize(width: 18, height: 18) else {
            throw NSError(domain: "TokenMeterMenuBar", code: 11, userInfo: [NSLocalizedDescriptionKey: "Splunk status-item chevron is invalid."])
        }
        let titleImage = statusTitleImage("$9.16 est · 36.2 tok/s")
        guard titleImage.isTemplate,
              titleImage.size.width > 100,
              titleImage.size.height == 18
        else {
            throw NSError(domain: "TokenMeterMenuBar", code: 12, userInfo: [NSLocalizedDescriptionKey: "Adaptive template status-title image is invalid."])
        }
        let data = try Data(contentsOf: tokenMeterMenubarURL)
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw NSError(domain: "TokenMeterMenuBar", code: 1, userInfo: [NSLocalizedDescriptionKey: "Response was not a JSON object."])
        }
        let snapshot = MeterSnapshot.fromJSON(dict)
        let budget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
        let quotas = (dict["provider_quotas"] as? [[String: Any]] ?? []).compactMap(ProviderQuota.fromJSON)
        let savedTab = MenuTab(rawValue: tokenMeterDefaults.string(forKey: selectedTabDefaultsKey) ?? "") ?? .run
        let savedMetrics: Set<TitleMetric> = {
            if let saved = tokenMeterDefaults.array(forKey: titleMetricsDefaultsKey) as? [String] {
                let parsed = Set(saved.compactMap(TitleMetric.init(rawValue:)))
                if !parsed.isEmpty { return parsed }
            }
            return [.cost, .speed]
        }()
        let alertsEnabled = tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil
            ? true : tokenMeterDefaults.bool(forKey: quotaAlertsEnabledDefaultsKey)
        let savedThreshold = tokenMeterDefaults.integer(forKey: quotaAlertThresholdDefaultsKey)
        let alertThreshold = [80, 90, 95].contains(savedThreshold) ? savedThreshold : 80
        let constrained = quotas
            .filter(\.fresh)
            .flatMap { provider in provider.windows.map { (provider: provider, window: $0) } }
            .max { $0.window.usedPercent < $1.window.usedPercent }
        let baseTitle = TitleMetric.allCases.compactMap { metric -> String? in
            guard savedMetrics.contains(metric) else { return nil }
            switch metric {
            case .cost: return snapshot.costLabel
            case .speed: return snapshot.outputSpeedLabel
            case .context: return snapshot.contextLabel
            case .model: return snapshot.model
            case .limits:
                guard let constrained = constrained else { return nil }
                return "\(constrained.provider.label) \(constrained.window.percentLabel) · \(constrained.window.compactKind)"
            }
        }.joined(separator: " · ")
        let activeTitle = budget?.anyExceeded == true ? "⚠︎ \(baseTitle)" : baseTitle
        print("native-brand=Splunk wordmark=\(Int(wordmark.size.width))x\(Int(wordmark.size.height)) chevron=template status-title=adaptive-template")
        print(snapshot.statusTitle)
        print(snapshot.outputSpeedLabel)
        print("active-title=\(activeTitle)")
        print("budget-state=\(budget?.compactLabel ?? "unconfigured") exceeded=\(budget?.anyExceeded == true)")
        print("tab=\(savedTab.title) title-metrics=\(TitleMetric.allCases.filter(savedMetrics.contains).map(\.title).joined(separator: ","))")
        print("quota-alerts=\(alertsEnabled ? "on" : "off") warn-at=\(alertThreshold)%")
        for provider in quotas {
            let windows = provider.windows.map { "\($0.label)=\($0.percentLabel)" }.joined(separator: ",")
            let coverage = provider.coverageNote.isEmpty ? "complete" : provider.coverageNote
            print("quota=\(provider.label) status=\(provider.status) windows=\(windows.isEmpty ? "none" : windows) coverage=\(coverage)")
        }
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
