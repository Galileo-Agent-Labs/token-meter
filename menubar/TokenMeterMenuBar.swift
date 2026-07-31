import Cocoa
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

enum MenuTab: String, CaseIterable {
    case run
    case overview
    case claude
    case codex
    case cursor

    var title: String {
        switch self {
        case .run: return "Run"
        case .overview: return "All"
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
    private let menuWidth: CGFloat = 420
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var pinnedSessionID = tokenMeterDefaults.string(forKey: pinnedSessionDefaultsKey)
    private var recentSessions: [RecentSession] = []
    private var providerQuotas: [ProviderQuota] = []
    private var selectedTab = MenuTab(
        rawValue: tokenMeterDefaults.string(forKey: selectedTabDefaultsKey) ?? ""
    ) ?? .run
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

        addTabPicker()
        menu.addItem(.separator())

        switch selectedTab {
        case .run:
            addRunMenu()
        case .overview:
            addOverviewMenu()
        case .claude, .codex, .cursor:
            addProviderMenu(selectedTab.rawValue)
        }

        menu.addItem(.separator())
        addSettingsMenu()
        addAction("Quit Token Meter Menubar", #selector(quit))
    }

    private func addRunMenu() {
        addHeader()
        menu.addItem(.separator())

        addAction("Open Dashboard", #selector(openDashboard))
        addAction("Open Daily Brief", #selector(openDailyBrief))
        addAction("Open Budget Settings", #selector(openBudgetSettings))
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
            if let budget = monthlyBudget {
                addMetricRow("Monthly budget", budget.compactLabel,
                    valueColor: .labelColor,
                    strong: budget.anyExceeded,
                    toolTip: budget.toolTip
                )
            }
        } else {
            addSignalRow("Connection", snapshot.error, color: .tokenMeterBlue)
        }
    }

    private func updateStatusTitle() {
        let title = selectedStatusTitle()
        let budgetExceeded = monthlyBudget?.anyExceeded == true
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: budgetExceeded ? NSColor.white : NSColor.labelColor,
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        ]
        let attributedTitle = NSMutableAttributedString(string: title, attributes: attrs)
        if budgetExceeded {
            let warningRange = (title as NSString).range(of: "⚠︎")
            if warningRange.location != NSNotFound {
                attributedTitle.addAttribute(.foregroundColor, value: NSColor.systemRed, range: warningRange)
            }
        }
        statusItem.button?.attributedTitle = attributedTitle
        var toolTip = snapshot.statusTooltip
        if let limits = limitsStatusTooltip() { toolTip += "\nLimits: \(limits)" }
        if let budget = monthlyBudget { toolTip += "\nBudget: \(budget.toolTip)" }
        statusItem.button?.toolTip = toolTip
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

    private func addTabPicker() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 42))
        let tabs = MenuTab.allCases
        let control = NSSegmentedControl(
            labels: tabs.map(\.title),
            trackingMode: .selectOne,
            target: self,
            action: #selector(selectTab(_:))
        )
        control.frame = NSRect(x: 12, y: 7, width: menuWidth - 24, height: 28)
        control.segmentStyle = .rounded
        control.controlSize = .small
        control.selectedSegment = tabs.firstIndex(of: selectedTab) ?? 0
        for index in tabs.indices {
            control.setWidth((menuWidth - 24) / CGFloat(tabs.count), forSegment: index)
        }
        view.addSubview(control)
        addViewItem(view)
    }

    private func addOverviewMenu() {
        let ranked = providerQuotas.sorted { lhs, rhs in
            let left = lhs.fresh ? lhs.highestWindow?.usedPercent ?? -1 : -1
            let right = rhs.fresh ? rhs.highestWindow?.usedPercent ?? -1 : -1
            if left == right { return lhs.label < rhs.label }
            return left > right
        }
        if let constrained = mostConstrainedQuota() {
            addSignalRow(
                "Most constrained",
                "\(constrained.provider.label) · \(constrained.window.label) · \(constrained.window.percentLabel) used",
                color: .labelColor
            )
            menu.addItem(.separator())
        } else if providerQuotas.allSatisfy({ $0.status != "loading" }) {
            addSignalRow("Provider limits", "No fresh provider-reported quota is available.", color: .secondaryLabelColor)
            menu.addItem(.separator())
        }

        for provider in ranked {
            if let window = provider.highestWindow {
                let suffix: String
                if provider.stale { suffix = " · stale" }
                else if provider.status == "error" { suffix = " · last good" }
                else { suffix = "" }
                addMetricRow(provider.label, "\(window.percentLabel) · \(window.label)\(suffix)")
            } else {
                let value = provider.status == "loading" ? "loading…" : "unavailable"
                addMetricRow(provider.label, value, valueColor: .secondaryLabelColor)
            }
        }
        if providerQuotas.isEmpty {
            addSignalRow("Provider limits", "Loading provider quotas.", color: .secondaryLabelColor)
        }
        addFooterRow("Only provider-reported limits are shown · refreshes every minute")
    }

    private func addProviderMenu(_ providerID: String) {
        guard let provider = providerQuotas.first(where: { $0.id == providerID }) else {
            addSignalRow(providerID.capitalized, "Loading provider quotas.", color: .secondaryLabelColor)
            return
        }
        let plan = provider.plan.trimmingCharacters(in: .whitespacesAndNewlines)
        addProviderHeader(
            provider.label,
            subtitle: [plan.isEmpty ? nil : plan, provider.freshnessLabel].compactMap { $0 }.joined(separator: " · ")
        )

        if provider.windows.isEmpty {
            let message = provider.error.isEmpty ? "No provider-reported quota window is available." : provider.error
            addSignalRow("Quota unavailable", message, color: .secondaryLabelColor)
        } else {
            for window in provider.windows {
                addQuotaWindowRow(window, stale: provider.stale || provider.status == "error")
            }
            if provider.status == "error", !provider.error.isEmpty {
                addFooterRow("Last refresh failed · showing last good values")
            }
        }
        if !provider.coverageNote.isEmpty {
            addSignalRow("Coverage", provider.coverageNote, color: .secondaryLabelColor)
        }

        let provenance = provider.provenance == "provider_reported" ? "Provider-reported" : "Unavailable"
        addFooterRow("\(provenance) · \(provider.source) · \(provider.freshnessLabel)")
    }

    private func addProviderHeader(_ title: String, subtitle: String) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 48))
        let titleLabel = label(title, frame: NSRect(x: 14, y: 24, width: menuWidth - 28, height: 18),
                               font: .systemFont(ofSize: 14, weight: .semibold), color: .labelColor)
        let subtitleLabel = label(subtitle, frame: NSRect(x: 14, y: 6, width: menuWidth - 28, height: 16),
                                  font: .systemFont(ofSize: 11.5, weight: .regular), color: .secondaryLabelColor)
        view.addSubview(titleLabel)
        view.addSubview(subtitleLabel)
        addViewItem(view)
    }

    private func addQuotaWindowRow(_ window: QuotaWindow, stale: Bool) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 76))
        let title = label(window.label, frame: NSRect(x: 14, y: 55, width: menuWidth - 105, height: 16),
                          font: .systemFont(ofSize: 12.5, weight: .semibold), color: .labelColor)
        let percent = label(window.percentLabel, frame: NSRect(x: menuWidth - 90, y: 54, width: 76, height: 18),
                            font: .monospacedDigitSystemFont(ofSize: 13, weight: .semibold),
                            color: stale ? .secondaryLabelColor : .labelColor)
        percent.alignment = .right

        let track = NSView(frame: NSRect(x: 14, y: 42, width: menuWidth - 28, height: 6))
        track.wantsLayer = true
        track.layer?.cornerRadius = 3
        track.layer?.backgroundColor = NSColor.separatorColor.withAlphaComponent(0.45).cgColor
        let fill = NSView(frame: NSRect(
            x: 0, y: 0,
            width: (menuWidth - 28) * CGFloat(window.usedPercent / 100), height: 6
        ))
        fill.wantsLayer = true
        fill.layer?.cornerRadius = 3
        fill.layer?.backgroundColor = NSColor.tokenMeterBlue.withAlphaComponent(stale ? 0.45 : 1).cgColor
        track.addSubview(fill)

        let reset = label(window.resetLabel, frame: NSRect(x: 14, y: 22, width: menuWidth - 28, height: 15),
                          font: .systemFont(ofSize: 11.5), color: .secondaryLabelColor)
        let paceText = window.pace?.summary ?? "Pace forecast unavailable"
        let pace = label(paceText, frame: NSRect(x: 14, y: 5, width: menuWidth - 28, height: 15),
                         font: .systemFont(ofSize: 11.5), color: .secondaryLabelColor)
        view.addSubview(title)
        view.addSubview(percent)
        view.addSubview(track)
        view.addSubview(reset)
        view.addSubview(pace)
        addViewItem(view)
    }

    private func addFooterRow(_ text: String) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 27))
        let footer = label(text, frame: NSRect(x: 14, y: 5, width: menuWidth - 28, height: 16),
                           font: .systemFont(ofSize: 10.5), color: .tertiaryLabelColor)
        view.addSubview(footer)
        addViewItem(view)
    }

    private func addSettingsMenu() {
        let settingsItem = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        let settingsMenu = NSMenu(title: "Settings")

        let modelPrices = NSMenuItem(title: "Model Prices", action: #selector(openModelPrices), keyEquivalent: "")
        modelPrices.target = self
        settingsMenu.addItem(modelPrices)
        settingsMenu.addItem(.separator())

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

        let thresholdItem = NSMenuItem(title: "Warn at", action: nil, keyEquivalent: "")
        let thresholdMenu = NSMenu(title: "Warn at")
        for threshold in [80, 90, 95] {
            let item = NSMenuItem(title: "\(threshold)%", action: #selector(setQuotaThreshold(_:)), keyEquivalent: "")
            item.target = self
            item.tag = threshold
            item.state = quotaAlertThreshold == threshold ? .on : .off
            thresholdMenu.addItem(item)
        }
        thresholdItem.submenu = thresholdMenu
        settingsMenu.addItem(thresholdItem)

        settingsItem.submenu = settingsMenu
        menu.addItem(settingsItem)
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

    @objc private func selectTab(_ sender: NSSegmentedControl) {
        let tabs = MenuTab.allCases
        guard tabs.indices.contains(sender.selectedSegment) else { return }
        selectedTab = tabs[sender.selectedSegment]
        tokenMeterDefaults.set(selectedTab.rawValue, forKey: selectedTabDefaultsKey)
        DispatchQueue.main.async { [weak self] in self?.rebuildMenu() }
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
