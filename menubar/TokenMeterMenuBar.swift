import Cocoa
import Carbon.HIToolbox
import Foundation

private let tokenMeterMenubarURL = URL(string: "http://127.0.0.1:8722/menubar")!
private let tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#sessions")!
private let tokenMeterBudgetSettingsURL = URL(string: "http://127.0.0.1:8722/#settings-budgets")!
private let pinnedSessionDefaultsKey = "TokenMeterPinnedSessionID"
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
            return "\(scope.label) · \(Int(scope.percent.rounded()))%"
        }
        if exceeded { return "Overall · \(Int(percent.rounded()))%" }
        let prefix = lowerBound ? "≥" : ""
        return "\(prefix)\(Int(percent.rounded()))%"
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

    var menuTitle: String {
        let maximumNameLength = 36
        let cleanName = identifier
        let clippedName = cleanName.count > maximumNameLength
            ? String(cleanName.prefix(maximumNameLength - 1)) + "…"
            : cleanName
        return "\(providerName) · \(clippedName)"
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
    var outputSpeedLive: Bool
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
            outputSpeedLive: false,
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
        let liveThroughput = dict["live_throughput"] as? [String: Any] ?? [:]
        let outputSpeedLive = throughputAvailable && bool(liveThroughput["available"])
        let selectedThroughput = outputSpeedLive ? liveThroughput : throughput
        let outputTokensPerSecond = throughputAvailable && bool(selectedThroughput["available"])
            ? optionalDouble(selectedThroughput["output_tps"])
            : nil
        let outputSpeedBasis = string(selectedThroughput["basis"]) ?? "unavailable"
        let outputSpeedSamples = outputSpeedLive
            ? int(liveThroughput["completed_steps"])
            : int(throughput["sample_count"])
        let outputSpeedCoverage = throughputAvailable && !outputSpeedLive && bool(throughput["available"])
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
            outputSpeedLive: outputSpeedLive,
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
        if outputSpeedLive {
            let stepWord = outputSpeedSamples == 1 ? "step" : "steps"
            return "Live rolling output pace from \(outputSpeedSamples) completed \(stepWord) in the active response. Tool and reasoning time may be included. This provisional value is replaced by the final completed-response measurement."
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

private func contextSignalColor(_ value: Double?) -> NSColor {
    guard let value = value else { return .secondaryLabelColor }
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

final class TokenMeterMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let stateURL = tokenMeterMenubarURL
    private let dashboardURL = tokenMeterDashboardURL
    private let menu = NSMenu(title: "Token Meter")
    private let menuWidth: CGFloat = 330
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var pinnedSessionID = tokenMeterDefaults.string(forKey: pinnedSessionDefaultsKey)
    private var recentSessions: [RecentSession] = []
    private var providerQuotas: [ProviderQuota] = []
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
    private var menuIsOpen = false
    private var menuRefreshPending = false
    private var snapshot = MeterSnapshot.disconnected("Waiting for http://127.0.0.1:8722/menubar")
    private var monthlyBudget: MonthlyBudget?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if tokenMeterDefaults.object(forKey: statusItemPreferredPositionDefaultsKey) == nil {
            tokenMeterDefaults.set(statusItemInitialPreferredPosition, forKey: statusItemPreferredPositionDefaultsKey)
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.autosaveName = statusItemAutosaveName
        statusItem.menu = menu
        menu.delegate = self
        if let button = statusItem.button {
            button.image = splunkChevronImage()
            button.imagePosition = .imageOnly
            button.contentTintColor = .tokenMeterBlue
        }
        registerGlobalHotKey()
        rebuildMenu()
        fetchState()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.fetchState()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        unregisterGlobalHotKey()
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
                self.providerQuotas = (dict["provider_quotas"] as? [[String: Any]] ?? [])
                    .compactMap(ProviderQuota.fromJSON)
                self.snapshot = MeterSnapshot.fromJSON(dict)
                self.monthlyBudget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
                self.evaluateQuotaNotifications()
                self.evaluateBudgetNotifications()
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
        updateStatusTitle()
        guard !menuIsOpen else {
            menuRefreshPending = true
            return
        }
        rebuildMenu()
    }

    private func openMenu() {
        rebuildMenu()
        NSApp.activate(ignoringOtherApps: true)
        statusItem.button?.performClick(nil)
    }

    private func rebuildMenu() {
        updateStatusTitle()
        menu.removeAllItems()

        addHeader()
        menu.addItem(.separator())
        addAction("Open Dashboard", #selector(openDashboard))
        menu.addItem(.separator())

        addSessionPicker()
        menu.addItem(.separator())

        if snapshot.connected {
            addMetricRow("Cost", snapshot.costLabel, toolTip: snapshot.pricingNote)
            let contextDetail = snapshot.contextPct == nil
                ? "Unavailable"
                : "\(snapshot.contextLabel) · \(formatCompactInt(snapshot.contextTokens)) / \(formatCompactInt(snapshot.contextWindow))"
            addMetricRow(
                "Context",
                contextDetail,
                valueColor: contextSignalColor(snapshot.contextPct),
                toolTip: snapshot.contextPct == nil
                    ? "Context is not reported by this trace."
                    : "Measured context tokens in the active model window."
            )
        } else {
            addConnectionRow()
        }

        if let budget = monthlyBudget, budget.configured {
            let prefix = budget.anyExceeded ? "Budget alert" : "Monthly budget"
            let item = NSMenuItem(
                title: "\(prefix) · \(budget.compactLabel)",
                action: #selector(openBudgetSettings),
                keyEquivalent: ""
            )
            item.target = self
            item.image = menuSymbol(
                budget.anyExceeded ? "exclamationmark.triangle.fill" : "calendar",
                description: prefix
            )
            item.toolTip = budget.toolTip
            menu.addItem(item)
        }

        let limitsItem = NSMenuItem(title: "Provider limits", action: nil, keyEquivalent: "")
        limitsItem.image = menuSymbol("gauge.with.dots.needle.50percent", description: "Provider limits")
        limitsItem.submenu = makeLimitsMenu()
        if let limits = limitsStatusTooltip() { limitsItem.toolTip = limits }
        menu.addItem(limitsItem)

        menu.addItem(.separator())
        let moreItem = NSMenuItem(title: "More", action: nil, keyEquivalent: "")
        moreItem.image = menuSymbol("ellipsis.circle", description: "More Token Meter actions")
        moreItem.submenu = makeSettingsMenu()
        menu.addItem(moreItem)
        menu.addItem(.separator())
        addAction("Quit Token Meter", #selector(quit))
    }

    private func addHeader() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 46))
        let selectedSession = recentSessions.first { $0.id == snapshot.selectedSessionID }
        let titleText: String
        let subtitleText: String
        if snapshot.connected {
            titleText = selectedSession?.identifier ?? snapshot.menuTitle
            let following = pinnedSessionID == nil ? "Following latest" : "Following this session"
            subtitleText = "\(snapshot.provider) · \(following) · \(snapshot.idleLabel)"
        } else {
            titleText = "Token Meter"
            subtitleText = "Server offline"
        }
        let title = menuLabel(
            titleText,
            frame: NSRect(x: 14, y: 22, width: menuWidth - 28, height: 18),
            font: .systemFont(ofSize: 13, weight: .semibold),
            color: .labelColor
        )
        title.toolTip = titleText
        let subtitle = menuLabel(
            subtitleText,
            frame: NSRect(x: 14, y: 4, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 11.5, weight: .regular),
            color: .secondaryLabelColor
        )
        subtitle.toolTip = subtitleText
        view.addSubview(title)
        view.addSubview(subtitle)
        addViewItem(view)
    }

    private func addSessionPicker() {
        let heading = NSMenuItem(title: "Recent sessions", action: nil, keyEquivalent: "")
        heading.isEnabled = false
        menu.addItem(heading)

        let followLatest = NSMenuItem(title: "Follow latest", action: #selector(followLatest), keyEquivalent: "")
        followLatest.target = self
        followLatest.state = pinnedSessionID == nil ? .on : .off
        followLatest.image = menuSymbol("arrow.triangle.2.circlepath", description: "Follow latest")
        followLatest.toolTip = "Automatically follow the most recently active session."
        menu.addItem(followLatest)

        for session in recentSessions.prefix(5) {
            let item = NSMenuItem(title: session.menuTitle, action: #selector(pinSession(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = session.id
            item.state = pinnedSessionID == session.id ? .on : .off
            item.image = menuSymbol(session.symbolName, description: session.providerName)
            item.toolTip = "Follow this session · \(session.toolTip)"
            menu.addItem(item)
        }

        if recentSessions.isEmpty {
            let empty = NSMenuItem(title: "No recent sessions", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        }
    }

    private func makeLimitsMenu() -> NSMenu {
        let limitsMenu = NSMenu(title: "Provider limits")
        guard !providerQuotas.isEmpty else {
            let empty = NSMenuItem(title: "No provider limits reported", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            limitsMenu.addItem(empty)
            return limitsMenu
        }

        for provider in providerQuotas {
            let summary = provider.highestWindow.map { " · \($0.percentLabel) max" } ?? ""
            let providerItem = NSMenuItem(title: "\(provider.label)\(summary)", action: nil, keyEquivalent: "")
            providerItem.image = menuSymbol(providerSymbol(provider.id), description: provider.label)
            let providerMenu = NSMenu(title: provider.label)
            if provider.windows.isEmpty {
                let unavailable = provider.error.isEmpty ? provider.freshnessLabel : provider.error
                let item = NSMenuItem(title: unavailable, action: nil, keyEquivalent: "")
                item.isEnabled = false
                item.toolTip = provider.coverageNote
                providerMenu.addItem(item)
            } else {
                for window in provider.windows {
                    let item = NSMenuItem(
                        title: "\(window.label) · \(window.percentLabel) used",
                        action: nil,
                        keyEquivalent: ""
                    )
                    item.isEnabled = false
                    item.toolTip = [window.resetLabel, window.pace?.summary, provider.freshnessLabel]
                        .compactMap { $0 }
                        .joined(separator: " · ")
                    providerMenu.addItem(item)
                }
            }
            providerItem.submenu = providerMenu
            providerItem.toolTip = provider.coverageNote
            limitsMenu.addItem(providerItem)
        }
        return limitsMenu
    }

    private func addMetricRow(
        _ name: String,
        _ value: String,
        valueColor: NSColor = .labelColor,
        toolTip: String? = nil
    ) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 25))
        view.toolTip = toolTip
        let nameLabel = menuLabel(
            name,
            frame: NSRect(x: 14, y: 4, width: 90, height: 17),
            font: .systemFont(ofSize: 12, weight: .regular),
            color: .secondaryLabelColor
        )
        let valueLabel = menuLabel(
            value,
            frame: NSRect(x: 104, y: 3, width: menuWidth - 118, height: 18),
            font: .monospacedDigitSystemFont(ofSize: 12.5, weight: .semibold),
            color: valueColor
        )
        valueLabel.alignment = .right
        nameLabel.toolTip = toolTip
        valueLabel.toolTip = toolTip
        view.addSubview(nameLabel)
        view.addSubview(valueLabel)
        addViewItem(view)
    }

    private func addConnectionRow() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 42))
        let title = menuLabel(
            "Token Meter is not reachable",
            frame: NSRect(x: 14, y: 22, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 12, weight: .semibold),
            color: .labelColor
        )
        let detail = menuLabel(
            snapshot.error,
            frame: NSRect(x: 14, y: 4, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 11.5, weight: .regular),
            color: .secondaryLabelColor
        )
        view.addSubview(title)
        view.addSubview(detail)
        addViewItem(view)
    }

    private func menuLabel(_ text: String, frame: NSRect, font: NSFont, color: NSColor) -> NSTextField {
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

    private func menuSymbol(_ name: String, description: String) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: description)
        image?.isTemplate = true
        return image
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
                    delegate.openMenu()
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

    func runMenuSmoke() throws {
        let originalTitleMetrics = titleMetrics
        defer {
            titleMetrics = originalTitleMetrics
            timer?.invalidate()
            menu.cancelTracking()
            statusItem = nil
        }
        titleMetrics = Set(TitleMetric.allCases)
        let smokeData = try Data(contentsOf: stateURL)
        let smokeObject = try JSONSerialization.jsonObject(with: smokeData)
        guard let smokePayload = smokeObject as? [String: Any] else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 12,
                userInfo: [NSLocalizedDescriptionKey: "Native menu smoke payload was not a JSON object."]
            )
        }
        snapshot = MeterSnapshot.fromJSON(smokePayload)
        monthlyBudget = MonthlyBudget.fromJSON(smokePayload["budget"] as? [String: Any])
        providerQuotas = (smokePayload["provider_quotas"] as? [[String: Any]] ?? [])
            .compactMap(ProviderQuota.fromJSON)
        recentSessions = (smokePayload["recent_sessions"] as? [[String: Any]] ?? [])
            .compactMap(RecentSession.fromJSON)
        rebuildMenu()
        let expectedTitle = selectedStatusTitle()
        let renderedTitle = statusItem.button?.accessibilityValue() as? String

        guard statusItem.menu === menu,
              renderedTitle == expectedTitle,
              menu.items.contains(where: { $0.title == "Follow latest" }),
              menu.items.filter({ $0.representedObject is String }).count == recentSessions.prefix(5).count,
              menu.items.contains(where: { $0.title == "Provider limits" && $0.submenu != nil }),
              menu.items.contains(where: { $0.title == "More" && $0.submenu != nil }),
              menu.items.contains(where: { $0.title == "Quit Token Meter" && $0.action == #selector(quit) })
        else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "Native menu did not expose direct session following and compact submenus."]
            )
        }
        print("native-menu-title=\(expectedTitle)")
    }

    private func updateStatusTitle() {
        let title = selectedStatusTitle()
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
        if snapshot.throughputAvailable { toolTip += "\n\(snapshot.outputSpeedTooltip)" }
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
        return "\(constrained.provider.label) \(constrained.window.percentLabel)"
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

    private func makeSettingsMenu() -> NSMenu {
        let settingsMenu = NSMenu(title: "Settings")

        let dailyBrief = NSMenuItem(title: "Open Daily Brief", action: #selector(openDailyBrief), keyEquivalent: "")
        dailyBrief.target = self
        settingsMenu.addItem(dailyBrief)
        let tools = NSMenuItem(title: "Open Tools & Skills", action: #selector(openToolsAndSkills), keyEquivalent: "")
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

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_MENU_SMOKE"] == "1" {
    let app = NSApplication.shared
    let delegate = TokenMeterMenuBar()
    app.delegate = delegate
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
        do {
            try delegate.runMenuSmoke()
            print("native-menu=ready sessions=direct follow-latest=direct")
            exit(0)
        } catch {
            fputs("Token Meter native menu smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    app.run()
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" {
    do {
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
        let sessions = (dict["recent_sessions"] as? [[String: Any]] ?? []).compactMap(RecentSession.fromJSON)
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
                return "\(constrained.provider.label) \(constrained.window.percentLabel)"
            }
        }.joined(separator: " · ")
        let activeTitle = budget?.anyExceeded == true ? "⚠︎ \(baseTitle)" : baseTitle
        print("native-menu=system chevron=template status-title=adaptive-template sessions=\(sessions.prefix(5).count) direct-follow=true")
        print(snapshot.statusTitle)
        print(snapshot.outputSpeedLabel)
        print("active-title=\(activeTitle)")
        print("budget-state=\(budget?.compactLabel ?? "unconfigured") exceeded=\(budget?.anyExceeded == true)")
        print("title-metrics=\(TitleMetric.allCases.filter(savedMetrics.contains).map(\.title).joined(separator: ","))")
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
