import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useMemo, useState } from "react";

/* ------------------------------------------------------------------ 类型 */

interface BehaviourState {
  supported: string[];
  active: string | null;
}

interface BatteryState {
  battery: string;
  battery_path: string;
  capacity?: string;
  status?: string;
  power_now?: string;
  power_watts?: number;
  charge_behaviour: BehaviourState;
  behaviour_raw: string | null;
  behaviour_node: boolean;
  threshold: number | null;
  threshold_node: boolean;
  supports_normal: boolean;
  supports_awake_bypass: boolean;
  supports_bypass: boolean;
  supports_force_discharge: boolean;
  supports_threshold: boolean;
  ac_online: boolean | null;
  ac_node: string | null;
  auto_restore: boolean;
  saved_threshold: number | null;
  saved_mode: string | null;
  restore_report: string[];
  presets: number[];
  threshold_disabled_value: number;
}

interface Result {
  ok: boolean;
  data?: BatteryState;
  error?: string;
}

/* ------------------------------------------------------------------ RPC */

const getStatus = callable<[], Result>("get_status");
const setChargeMode = callable<[mode: string], Result>("set_charge_mode");
const setChargeThreshold = callable<[value: number], Result>("set_charge_threshold");
const setAutoRestore = callable<[enabled: boolean], Result>("set_auto_restore");

/* -------------------------------------------------------------- 辅助函数 */

const MODE_LABELS: Record<string, string> = {
  auto: "正常充电",
  "inhibit-charge-awake": "开机旁路",
  "inhibit-charge": "始终旁路",
  "force-discharge": "强制放电",
};

const MODE_DESCRIPTIONS: Record<string, string> = {
  auto: "正常向电池充电，并遵循下方设置的充电上限。",
  "inhibit-charge-awake": "运行时停止充电、直接由电源供电；设备睡眠并接电时恢复充电。",
  "inhibit-charge": "运行和睡眠状态都停止充电，适合长期插电使用。",
  "force-discharge": "接电状态下强制放电。",
};

const statusName = (value?: string) =>
  ({
    Charging: "充电中",
    Discharging: "放电中",
    Full: "已充满",
    "Not charging": "未充电",
    Unknown: "未知",
  }[value ?? ""] ??
    value ??
    "--");

const modeLabel = (mode: string | null) => (mode ? MODE_LABELS[mode] ?? mode : "未知");

const REFRESH_INTERVAL_MS = 5000;

/** 与后端 THRESHOLD_DISABLED 保持一致：写入该值即解除上限。 */
const THRESHOLD_DISABLED = 100;

/** 失败提示的弹出时长；同时保留在通知中心，便于回看。 */
const TOAST_FAILURE_MS = 6000;

/**
 * 弹出一次失败提示；切换成功时不弹任何通知。
 *
 * 成功不提示的理由：每次写入后后端都会重读内核节点，界面上的模式和上限就是
 * 内核里的真实状态，本身已经是反馈了。
 *
 * 而 Decky 的 toaster 除了右下角弹出，还会把通知塞进 Steam 的通知中心
 * （toaster 内部调用 NotificationStore.ProcessNotification 并 unshift 到 tray），
 * 于是每成功切换一次就留下一条「已切换…」，很快堆成一长串。
 * 只在失败时提示，既没有堆积问题，也不会漏掉真正需要看到的错误。
 *
 * 失败提示**故意不传** `expiration`，让它留在通知中心可回看。
 */
const notifyFailure = (message: string) => {
  toaster.toast({
    title: "F1 Pro 电池",
    body: message,
    duration: TOAST_FAILURE_MS,
  });
};

const BatteryIcon = () => (
  <svg
    width="19"
    height="19"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.9"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="2" y="7" width="16" height="10" rx="2.5" />
    <path d="M21 10.5v3" />
    <path d="M10.5 9.5L7.5 12.5h3.5l-3 3" />
  </svg>
);

/* -------------------------------------------------------------- 主界面 */

function Content() {
  const [state, setState] = useState<BatteryState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** 统一处理 RPC 结果；成功返回 null，失败返回错误文案。 */
  const apply = (result: Result, fallback: string): string | null => {
    if (result.ok && result.data) {
      setState(result.data);
      setError(null);
      return null;
    }
    const message = result.error ?? fallback;
    setError(message);
    return message;
  };

  const refresh = async () => {
    apply(await getStatus(), "读取电池状态失败");
  };

  // 首次挂载后读取一次，之后定时轮询，保证界面与内核真实状态一致。
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const result = await getStatus();
      if (!cancelled) apply(result, "读取电池状态失败");
    };
    tick();
    const timer = setInterval(tick, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const changeMode = async (mode: string) => {
    setBusy(true);
    const problem = apply(await setChargeMode(mode), "切换充电模式失败");
    setBusy(false);
    if (problem) notifyFailure(problem);
  };

  const changeThreshold = async (value: number) => {
    setBusy(true);
    const problem = apply(await setChargeThreshold(value), "设置充电上限失败");
    setBusy(false);
    if (problem) notifyFailure(problem);
  };

  const changeAutoRestore = async (enabled: boolean) => {
    setBusy(true);
    apply(await setAutoRestore(enabled), "保存设置失败");
    setBusy(false);
  };

  const active = state?.charge_behaviour.active ?? null;

  const thresholdValue = useMemo(() => {
    if (typeof state?.threshold === "number") return state.threshold;
    if (typeof state?.saved_threshold === "number") return state.saved_threshold;
    return undefined;
  }, [state?.threshold, state?.saved_threshold]);

  const thresholdOptions = useMemo(
    () => [
      ...(state?.presets ?? [50, 60, 70, 80, 85, 90, 95]).map((value) => ({
        label: `${value}%`,
        data: value,
      })),
      { label: `不限制（充满到 ${THRESHOLD_DISABLED}%）`, data: THRESHOLD_DISABLED },
    ],
    [state?.presets],
  );

  if (error && !state) {
    return (
      <PanelSection title="F1 Pro Battery">
        <PanelSectionRow>
          <div style={{ whiteSpace: "normal", lineHeight: 1.35 }}>{error}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={refresh}>
            重新读取
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const capacity = state?.capacity ?? "--";

  const acOnline: boolean | null = state?.ac_online ?? null;

  /**
   * 电池当前没有在充也没有在放：
   *
   * - 旁路模式（内核禁止充电）；
   * - 正常充电模式下内核报 `Not charging`，最常见的是**已达设定上限**。
   *
   * 这两种情况的物理状态完全一样——外接电源供整机、电池闲置，所以界面统一显示
   * 「供电中」，不区分原因（原因在下一行的「当前模式」里已经有了）。
   */
  const onAdapter =
    active === "inhibit-charge" ||
    active === "inhibit-charge-awake" ||
    state?.status === "Not charging";

  /** 外接电源在线；拿不到探测节点时（acOnline 为 null）退回看 status 是否在放电。 */
  const adapterLive =
    acOnline === true || (acOnline === null && state?.status !== "Discharging");

  /**
   * 电源直供：外接电源在带整机，电池不充不放。
   *
   * 此时电池侧的 power_now 在内核里不再更新（读数会停在切换前的旧值），
   * 显示它只会误导，所以这一档不出数字。
   *
   * 例外：外接电源**确认已断开**却报 `Not charging`，是自相矛盾的读数，
   * 此时不硬说成「供电中」，退回「未充电」。
   */
  const supplyOnly = onAdapter && adapterLive;

  /**
   * 只有电池确实在充/放电时，power_now 才是可信的实时值。
   *
   * 正数 = 充入电池，负数 = 电池放电；符号由后端依据 status 补齐，
   * 因为 ACPI 电池驱动的 power_now 恒为正值。
   * 已充满、供电中时电池电流≈0，读数没有意义（且往往停留在旧值），所以不出数字。
   */
  const flowing = state?.status === "Charging" || state?.status === "Discharging";

  const watts =
    !supplyOnly && flowing && typeof state?.power_watts === "number"
      ? `${state.power_watts >= 0 ? "" : "-"}${Math.abs(state.power_watts).toFixed(1)} W`
      : null;

  return (
    <>
      <PanelSection title="电池状态">
        <PanelSectionRow>
          <div style={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
            <span>{state?.battery ?? "BAT"}</span>
            <span>{capacity}%</span>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ whiteSpace: "normal", lineHeight: 1.35 }}>
            {supplyOnly ? "供电中" : statusName(state?.status)}
            {watts ? ` · ${watts}` : ""} · 上限{" "}
            {typeof thresholdValue === "number" ? `${thresholdValue}%` : "不限制"}
            <br />
            当前模式：<b>{modeLabel(active)}</b>
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="充电模式">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !state?.supports_normal}
            onClick={() => changeMode("auto")}
          >
            {active === "auto" ? "✓ 正常充电" : "正常充电"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !state?.supports_awake_bypass}
            onClick={() => changeMode("inhibit-charge-awake")}
          >
            {active === "inhibit-charge-awake" ? "✓ 开机旁路" : "开机旁路"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !state?.supports_bypass}
            onClick={() => changeMode("inhibit-charge")}
          >
            {active === "inhibit-charge" ? "✓ 始终旁路" : "始终旁路"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }}>
            {MODE_DESCRIPTIONS[active ?? ""] ?? ""}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="充电上限">
        <PanelSectionRow>
          <DropdownItem
            label="停止充电电量"
            rgOptions={thresholdOptions}
            selectedOption={thresholdValue ?? state?.threshold_disabled_value ?? 100}
            disabled={busy || !state?.supports_threshold}
            onChange={(option: { data: number }) => changeThreshold(option.data)}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="设置">
        <PanelSectionRow>
          <ToggleField
            label="Decky 启动时自动恢复"
            description="重新应用上次保存的充电模式与充电上限"
            checked={state?.auto_restore ?? true}
            disabled={busy}
            onChange={changeAutoRestore}
          />
        </PanelSectionRow>
        {state?.restore_report && state.restore_report.length > 0 && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }}>
              {state.restore_report.join("；")}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {error && (
        <PanelSection title="提示">
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal", lineHeight: 1.35 }}>{error}</div>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="诊断">
        <PanelSectionRow>
          <div
            style={{
              whiteSpace: "normal",
              lineHeight: 1.35,
              fontFamily: "monospace",
              fontSize: "11px",
              opacity: 0.75,
            }}
          >
            {state?.battery_path ?? "未找到电池节点"}
            <br />
            外接电源: {state?.ac_node ?? "无节点"}
            {acOnline === null ? "" : acOnline ? " 在线" : " 离线"}
            <br />
            status: {state?.status ?? "不存在"}
            <br />
            charge_behaviour: {state?.behaviour_raw ?? "不存在"}
            <br />
            power_now: {state?.power_now ?? "不存在"}
            <br />
            threshold 节点: {state?.threshold_node ? "存在" : "不存在"}
            {state && !state.supports_awake_bypass ? (
              <>
                <br />
                内核未提供 inhibit-charge-awake
              </>
            ) : null}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={refresh}>
            刷新
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "F1 Pro Battery Control",
  titleView: <div className={staticClasses.Title}>F1 Pro 电池控制</div>,
  content: <Content />,
  icon: <BatteryIcon />,
}));
