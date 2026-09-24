const manifest = {"name":"F1Pro EC Control"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;
const toaster = api.toaster;
const definePlugin = (fn) => {
    return (...args) => {
        return fn(...args);
    };
};

/* ------------------------------------------------------------------ RPC */
const getStatus = callable("get_status");
const setChargeMode = callable("set_charge_mode");
const setChargeThreshold = callable("set_charge_threshold");
const setAutoRestore = callable("set_auto_restore");
const getFanStatus = callable("get_fan_status");
const getFanProfiles = callable("get_fan_profiles");
const setFanMode = callable("set_fan_mode");
const setFanCustomCurve = callable("set_fan_custom_curve");
/* -------------------------------------------------------------- 辅助函数 */
const MODE_LABELS = {
    auto: "正常充电",
    "inhibit-charge-awake": "开机旁路",
    "inhibit-charge": "始终旁路",
    "force-discharge": "强制放电",
};
const MODE_DESCRIPTIONS = {
    auto: "正常向电池充电，并遵循下方设置的充电上限。",
    "inhibit-charge-awake": "运行时停止充电、直接由电源供电；设备睡眠并接电时恢复充电。",
    "inhibit-charge": "运行和睡眠状态都停止充电，适合长期插电使用。",
    "force-discharge": "接电状态下强制放电。",
};
const statusName = (value) => ({
    Charging: "充电中",
    Discharging: "放电中",
    Full: "已充满",
    "Not charging": "未充电",
    Unknown: "未知",
}[value ?? ""] ??
    value ??
    "--");
const modeLabel = (mode) => (mode ? MODE_LABELS[mode] ?? mode : "未知");
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
const notifyFailure = (message) => {
    toaster.toast({
        title: "F1Pro EC Control",
        body: message,
        duration: TOAST_FAILURE_MS,
    });
};
const BatteryIcon = () => (SP_JSX.jsxs("svg", { width: "19", height: "19", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round", strokeLinejoin: "round", children: [SP_JSX.jsx("rect", { x: "2", y: "7", width: "16", height: "10", rx: "2.5" }), SP_JSX.jsx("path", { d: "M21 10.5v3" }), SP_JSX.jsx("path", { d: "M10.5 9.5L7.5 12.5h3.5l-3 3" })] }));
/* -------------------------------------------------------------- 风扇曲线 */
/** 曲线页里每个节点最多放这么多档 PWM 读取范围 */
const FAN_CURVE_POINTS = 5;
/**
 * 主页面与曲线页共用的模式短名。
 *
 * 后端也给了 `fan.mode_labels`，但那是给状态行走的文案；这里的短名要配按钮，
 * 所以单独定义一份。两边都保留是有意的——不要为了"减少重复"而让按钮文案
 * 跟着后端走，那会让中文/英文混排不受前端控制。
 */
const FAN_MODE_LABELS = {
    auto: "自动",
    quiet: "静音",
    balanced: "均衡",
    performance: "性能",
    custom: "自定义",
};
/**
 * 主页面上按顺序列出的风扇模式按钮。
 *
 * **每个模式独占一行**，宽度与「编辑风扇曲线」一致。别改回网格：
 * 早期版本用两列网格，真机上先后出现"裸 `1fr` 被 `ButtonItem` 的 min-content
 * 宽度撑破"和"`minmax(0, 1fr)` 下相邻按钮底色块重叠"两种问题 ——
 * `ButtonItem(layout="below")` 内部有一层我们控制不到的宽度约束。
 */
const FAN_MODE_ORDER = [
    "auto",
    "quiet",
    "balanced",
    "performance",
    "custom",
];
/** 自定义曲线的初始值；后端会通过 get_fan_profiles 给出真正的种子曲线。 */
const FAN_CURVE_SEED = [
    [40, 40],
    [50, 75],
    [60, 115],
    [70, 160],
    [80, 255],
];
/**
 * 判断一条曲线是否严格递增。
 *
 * **不要用 `sort` 去"修复"顺序**：把节点按温度重排会让每个节点的 PWM 跟着
 * 它的温度一起搬走，用户看到的是"高温度对应了更低的 PWM"这种静默错乱。
 * 温度是用户直接拖动出来的，顺序错了就该报错让他改，而不是替他猜。
 */
const curveIsStrictlyIncreasing = (points) => points.length > 0 &&
    points.every((point, index) => index === 0 || point[0] > points[index - 1][0]);
/**
 * 温度滑杆的可用上下界：夹在左右邻居之间（留 1°C 间隔，保证严格递增），
 * 同时不越出后端的整体约束。滑不出越界值，比滑完再报错体验好。
 */
const tempBoundsFor = (points, index, minTemp, maxTemp) => ({
    min: index === 0 ? minTemp : points[index - 1][0] + 1,
    max: index === points.length - 1 ? maxTemp : points[index + 1][0] - 1,
});
/** 单行两列的横向排版（温度 / RPM、模式 / PWM 之类的状态行）。 */
const rowStyle = {
    display: "flex",
    justifyContent: "space-between",
    width: "100%",
};
/** 需要换行的长文本块（错误提示、诊断信息）。 */
const blockStyle = {
    whiteSpace: "normal",
    lineHeight: 1.35,
};
/**
 * 把 QAM 面板的滚动位置拉回最顶端。
 *
 * **为什么不能直接 `window.scrollTo`**：Decky 插件并不是一个独立页面，
 * 它渲染在 Gamepad UI 的 QAM 面板里，真正滚动的是**面板自己的那个容器**
 * （在插件的 React 树之外，拿不到 ref）。`window` 上滚动没有任何效果。
 *
 * 所以从插件自己的根节点**向上找第一个可滚动的祖先**再复位。
 * 这个做法对 QAM 改版比较耐受：只要面板还是靠 `overflow` 滚动，
 * 无论具体是哪一层，都能找到。
 *
 * 复位时机也很关键：必须在**新视图渲染之后**（`useEffect` 里），
 * 此时新内容已经撑开、`scrollHeight` 才是对的；在点击那一刻复位会被
 * 随后的渲染覆盖掉。
 *
 * 找不到可滚动祖先时**什么都不做**，不抛错——最坏情况就是回到
 * "切换后位置不重置"的旧行为，不该因为面板结构变了就让整个界面崩掉。
 */
function scrollPanelToTop(node) {
    let current = node?.parentElement ?? null;
    while (current) {
        const overflowY = window.getComputedStyle(current).overflowY;
        const scrollable = overflowY === "auto" || overflowY === "scroll";
        // `scrollHeight > clientHeight` 用来排除"声明了 overflow 但当前没内容可滚"的层，
        // 否则会停在一个空容器上，真正的滚动层反而在更外层。
        if (scrollable && current.scrollHeight > current.clientHeight) {
            current.scrollTop = 0;
            return;
        }
        current = current.parentElement;
    }
}
/* -------------------------------------------------------------- 主界面 */
function Content() {
    const [state, setState] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState(null);
    const [busy, setBusy] = SP_REACT.useState(false);
    const [fan, setFan] = SP_REACT.useState(null);
    const [fanProfiles, setFanProfiles] = SP_REACT.useState(null);
    const [fanError, setFanError] = SP_REACT.useState(null);
    const [fanBusy, setFanBusy] = SP_REACT.useState(false);
    const [view, setView] = SP_REACT.useState("main");
    const [curve, setCurve] = SP_REACT.useState(FAN_CURVE_SEED);
    const [curveDirty, setCurveDirty] = SP_REACT.useState(false);
    const [curveError, setCurveError] = SP_REACT.useState(null);
    /** 插件自身那层 DOM，用来向上寻找真正在滚动的祖先。 */
    const rootRef = SP_REACT.useRef(null);
    /**
     * 是否已经由用户手动编辑过曲线。
     *
     * 5 秒一次的轮询会重新拉到后端保存的曲线；如果无脑覆盖本地编辑，用户
     * 拖到一半的滑杆就会被拽回去（后端此刻还是旧曲线）。所以只在用户没编辑过
     * 的时候才同步服务端值。
     */
    const curveTouched = SP_REACT.useRef(false);
    const apply = (result, fallback) => {
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
    /** 拉一次风扇状态；不可用时只记录原因，不当作操作失败弹通知。 */
    const refreshFan = async () => {
        const result = await getFanStatus();
        if (result.ok && result.data) {
            setFan(result.data);
            setFanError(result.data.available ? null : (result.data.reason ?? null));
            return result.data;
        }
        setFanError(result.error ?? "读取风扇状态失败");
        return null;
    };
    const refreshFanProfiles = async () => {
        const result = await getFanProfiles();
        if (!result.ok || !result.data)
            return;
        setFanProfiles(result.data);
        if (!curveTouched.current) {
            // 取曲线的优先级：**已保存的自定义曲线** > 当前模式的预设 > 默认种子。
            //
            // 顺序不能反：`profiles` 里**只有预设**（没有 "custom" 这个键），
            // 所以旧写法 `profiles[mode] ?? custom_seed` 在自定义模式下必然取不到、
            // 直接退回默认种子 —— 用户刚保存的曲线会被默认曲线覆盖。
            // 现在后端给了 custom_curve，它才是"用户当前真正的曲线"。
            const active = fan?.mode ?? "";
            const fromProfiles = result.data.profiles?.[active];
            const stored = active === "custom"
                ? (result.data.custom_curve ?? fromProfiles ?? result.data.custom_seed)
                : (fromProfiles ?? result.data.custom_curve ?? result.data.custom_seed);
            if (Array.isArray(stored) && stored.length === FAN_CURVE_POINTS) {
                setCurve(stored.map((point) => [...point]));
            }
        }
    };
    // 首次挂载后读取一次，之后定时轮询，保证界面与内核真实状态一致。
    //
    // 用**递归 setTimeout** 而不是 setInterval：setInterval 不看回调是否跑完，
    // 只要到点就再发一次。这里的 tick 要等在途 RPC（sysfs 读 + 可能的 EC 写），
    // 在 EC 卡顿时单次可能耗时远超间隔，请求就会越积越多 —— 界面看起来
    // 是"卡住后自己好了"，其实是排队的响应集中回来，几次状态互相覆盖。
    // 递归写法天然有背压：**上一次 resolve 之后才排下一次**，
    // 最坏情况下轮询频率自动降低，不会堆积。
    SP_REACT.useEffect(() => {
        let cancelled = false;
        let timer = null;
        const tick = async () => {
            if (cancelled)
                return;
            try {
                const batteryResult = await getStatus();
                if (!cancelled)
                    apply(batteryResult, "读取电池状态失败");
            }
            catch (error) {
                // **必须自己捕获**：RPC 层的 Promise 一旦被拒绝，就会变成一次
                // 未处理的 rejection；而且下面读风扇状态的代码会被整段跳过 ——
                // 电池读失败不该把风扇状态一起拖下水（两者是独立的 RPC）。
                // 注意只记原因、不弹通知：轮询每 5 秒一次，EC 卡顿时会刷屏。
                if (!cancelled) {
                    setFanError(error instanceof Error ? error.message : "读取状态失败");
                }
            }
            try {
                const fanResult = await getFanStatus();
                if (cancelled)
                    return;
                if (fanResult.ok && fanResult.data) {
                    setFan(fanResult.data);
                    setFanError(fanResult.data.available ? null : (fanResult.data.reason ?? null));
                    // 用户在曲线页拖滑杆时不要覆盖他的编辑。
                    if (!curveTouched.current) {
                        const active = fanResult.data.mode;
                        const fromProfiles = fanProfiles?.profiles?.[active];
                        const stored = fromProfiles ?? fanResult.data.curve ?? fanResult.data.custom_curve;
                        if (Array.isArray(stored) && stored.length === FAN_CURVE_POINTS) {
                            setCurve(stored.map((point) => [...point]));
                        }
                    }
                }
                else {
                    setFanError(fanResult.error ?? "读取风扇状态失败");
                }
            }
            catch (error) {
                // 同上：吞掉异常但不吞掉续排（续排由下面的 finally 负责）。
                if (!cancelled) {
                    setFanError(error instanceof Error ? error.message : "读取风扇状态失败");
                }
            }
            finally {
                // 必须放在 finally 里：中途抛错（RPC 层异常）时若不排下一次，
                // 轮询会**静默停死**，界面停在旧数据上再也不刷新。
                // 用 finally 保证无论成功失败都续上，与 setInterval 的语义对齐。
                if (!cancelled) {
                    timer = setTimeout(tick, REFRESH_INTERVAL_MS);
                }
            }
        };
        tick();
        return () => {
            cancelled = true;
            if (timer !== null)
                clearTimeout(timer);
        };
        // fanProfiles 只用于"有预设时优先取预设"，不需要因为它的变化重建轮询。
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    SP_REACT.useEffect(() => {
        refreshFanProfiles();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    // 下面几个操作都是「置 busy → await RPC → 清 busy」。
    // **busy 的清除必须在 finally 里**：RPC 的 Promise 一旦被拒绝，
    // `setBusy(false)` 就不会执行，界面上的按钮会**永久禁用**
    // （离线探针 .mut/repro_busy.mjs 复现过 4/4 个操作都会卡住）。
    // 异常本身也要落到 error / notifyFailure，让用户知道为什么没成功。
    const changeMode = async (mode) => {
        setBusy(true);
        try {
            const problem = apply(await setChargeMode(mode), "切换充电模式失败");
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "切换充电模式失败";
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    const changeThreshold = async (value) => {
        setBusy(true);
        try {
            const problem = apply(await setChargeThreshold(value), "设置充电上限失败");
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "设置充电上限失败";
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    const changeAutoRestore = async (enabled) => {
        setBusy(true);
        try {
            const problem = apply(await setAutoRestore(enabled), "保存设置失败");
            if (problem)
                notifyFailure(problem);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "保存设置失败";
            setError(message);
            notifyFailure(message);
        }
        finally {
            setBusy(false);
        }
    };
    /**
     * 切到某个风扇模式（含预设曲线）；成功静默，失败才提示。
     *
     * 切换**预设**模式时顺手把本地曲线换成该预设的节点，界面立刻跟着变，
     * 不必等下一轮轮询（最长 3.5 秒）。切到「自动 / 自定义」时不改曲线：
     * 自动没有曲线，自定义的曲线由服务端负责回填，本地擅自改会覆盖用户正在编辑的内容。
     *
     * **返回值表示这次切换是否成功**：调用方（`saveCurve`）靠它判断曲线是否
     * 真的在风扇上生效了。原先把异常吞掉只弹通知，于是"保存曲线 → 切自定义"
     * 这个两步动作里第二步失败时，调用方完全看不出来。
     */
    const changeFanMode = async (mode) => {
        setFanBusy(true);
        try {
            const problem = await applyFan(await setFanMode(mode), "切换风扇模式失败");
            if (problem) {
                notifyFailure(problem);
                return false;
            }
            // **只有 RPC 成功之后才动本地状态**：把预设曲线填进滑杆、清掉编辑标记。
            //
            // 原先这段写在 await 之前，于是"切模式失败"时用户的编辑已经被丢掉 ——
            // 曲线被换成预设值、`curveTouched` 变 false（下一次轮询就能覆盖）、
            // `curveDirty` 变 false（「保存为自定义并应用」按钮直接变灰），
            // 而 EC 其实还跑着旧模式。用户在滑杆上的修改**没有任何提示地消失了**
            // （离线探针 .mut/repro_front_leftovers.mjs 的 G1 复现过）。
            const profile = fanProfiles?.profiles?.[mode];
            if (Array.isArray(profile) && profile.length === FAN_CURVE_POINTS) {
                curveTouched.current = false;
                setCurve(profile.map((point) => [...point]));
                setCurveDirty(false);
                setCurveError(null);
            }
            return true;
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "切换风扇模式失败";
            setFanError(message);
            notifyFailure(message);
            return false;
        }
        finally {
            setFanBusy(false);
        }
    };
    /**
     * 换页后把滚动位置拉回顶端。
     *
     * QAM 面板切换内容时**不会**重置滚动位置：从主页滚到底点「编辑风扇曲线」，
     * 子页面虽然换掉了，滚动偏移却留着，于是直接落在界面末端 ——
     * 最上面的「← 返回」都看不见，很像卡住了。
     *
     * 放在 `useEffect` 里（依赖 `view`）而不是点击时立刻调：
     * 要等新视图渲染完、内容撑开之后复位才生效；点击那一刻复位会被
     * 随后的渲染覆盖掉。
     */
    SP_REACT.useEffect(() => {
        scrollPanelToTop(rootRef.current);
    }, [view]);
    const applyFan = async (result, fallback) => {
        if (result.ok && result.data) {
            setFan(result.data);
            setFanError(result.data.available ? null : (result.data.reason ?? null));
            return null;
        }
        const message = result.error ?? fallback;
        setFanError(message);
        return message;
    };
    /** 拖动某个节点的温度或 PWM。 */
    const updateCurvePoint = (index, axis, value) => {
        curveTouched.current = true;
        setCurveDirty(true);
        setCurveError(null);
        setCurve((previous) => previous.map((point, pointIndex) => {
            if (pointIndex !== index)
                return point;
            const next = [...point];
            next[axis] = value;
            return next;
        }));
    };
    /** 保存自定义曲线；校验失败只提示，不影响正在运行的风扇。 */
    const saveCurve = async () => {
        // 按**原样**提交，不做排序：排序会把节点的 PWM 跟着温度一起搬家，
        // 于是"高温对应低 PWM"这种错乱会被悄悄写进设置里。顺序不对就报错。
        const points = curve.map((point) => [...point]);
        if (!curveIsStrictlyIncreasing(points)) {
            const message = "曲线节点的温度必须严格递增（每个节点都要比前一个更高）";
            setCurveError(message);
            notifyFailure(message);
            return;
        }
        setFanBusy(true);
        setCurveError(null);
        try {
            const result = await setFanCustomCurve(points);
            if (!result.ok || !result.data) {
                const message = result.error ?? "保存自定义曲线失败";
                setCurveError(message);
                notifyFailure(message);
                return;
            }
            // **以 RPC 返回值作为唯一 UI 来源**，不要在这之后再拉预设去覆盖它：
            // refreshFanProfiles 取曲线的优先级里含 custom_curve，若后端还没来得及
            // 反映刚写的值，就会把界面拽回旧曲线。这里直接采信这次保存的结果。
            curveTouched.current = false;
            setCurve(result.data.curve.map((point) => [...point]));
            // 「保存为自定义并应用」是**两步**：曲线先落盘（上面这步已经成功），
            // 再切到「自定义」模式才算真正作用在风扇上。
            //
            // 未保存标记必须等**第二步成功之后**才清：
            // 原先 setCurveDirty(false) 夹在这里，于是切模式失败时按钮立刻变灰、
            // 看起来一切就绪，而 EC 还在跑旧模式 —— 用户连重试的入口都没有了。
            // 现在失败时保持 dirty=true（按钮仍可点）、并留下可见原因。
            //
            // 顺序也不能反：changeFanMode 会按预设覆盖曲线，只是 "custom" 不在预设里，
            // 所以这一步不会动刚保存的值。
            if (fan?.mode !== "custom") {
                const applied = await changeFanMode("custom");
                if (!applied) {
                    setCurveError("曲线已保存，但切换为自定义模式失败，尚未在风扇上生效");
                    return;
                }
            }
            else {
                // 已经在自定义模式：刷新一次状态，让 PWM / 温度显示跟上。
                await refreshFan();
            }
            setCurveDirty(false);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "保存自定义曲线失败";
            setCurveError(message);
            notifyFailure(message);
        }
        finally {
            setFanBusy(false);
        }
    };
    /**
     * 把预设曲线**载入滑杆**，不直接写进 EC。
     *
     * 这里刻意只改本地 state：让用户先看清这条曲线长什么样、还能接着微调，
     * 真正生效要等他按「保存为自定义并应用」。直接 `changeFanMode(mode)` 的话
     * 会切到**预设模式**（EC 侧按预设跑），滑杆上却没有对应数据，
     * 用户看到的曲线和实际在跑的不是一回事。
     */
    const loadPresetCurve = (mode) => {
        const preset = fanProfiles?.profiles?.[mode];
        if (!Array.isArray(preset) || preset.length !== FAN_CURVE_POINTS) {
            notifyFailure(`没有可用的${FAN_MODE_LABELS[mode] ?? mode}曲线`);
            return;
        }
        curveTouched.current = true; // 别让下一次轮询把刚载入的值覆盖掉
        setCurve(preset.map((point) => [...point]));
        setCurveError(null);
        setCurveDirty(true);
    };
    /** 还原成后端给出的默认曲线种子（同样只落到滑杆，要保存才生效）。 */
    const restoreSeedCurve = () => {
        const seed = fanProfiles?.custom_seed ?? FAN_CURVE_SEED;
        curveTouched.current = true;
        setCurve(seed.map((point) => [...point]));
        setCurveError(null);
        setCurveDirty(true);
    };
    const active = state?.charge_behaviour.active ?? null;
    const thresholdValue = SP_REACT.useMemo(() => {
        if (typeof state?.threshold === "number")
            return state.threshold;
        if (typeof state?.saved_threshold === "number")
            return state.saved_threshold;
        return undefined;
    }, [state?.threshold, state?.saved_threshold]);
    const thresholdOptions = SP_REACT.useMemo(() => [
        ...(state?.presets ?? [50, 60, 70, 80, 85, 90, 95]).map((value) => ({
            label: `${value}%`,
            data: value,
        })),
        { label: `不限制（充满到 ${THRESHOLD_DISABLED}%）`, data: THRESHOLD_DISABLED },
    ], [state?.presets]);
    /* ---------------------------------------------------------- 风扇曲线页 */
    if (view === "fan") {
        const constraints = fanProfiles?.constraints;
        const minTemp = constraints?.min_temp ?? 30;
        const maxTemp = constraints?.max_temp ?? 100;
        const minPwm = constraints?.min_pwm ?? 40;
        const maxPwm = constraints?.max_pwm ?? 255;
        const available = fan?.available ?? false;
        // 预设套用**先落到滑杆上**，让用户看清曲线长什么样、还能微调；
        // 真正写进 EC 要等他按「保存为自定义并应用」。
        const presetButtons = [
            ["quiet", "静音"],
            ["balanced", "均衡"],
            ["performance", "性能"],
        ];
        return (SP_JSX.jsxs("div", { ref: rootRef, children: [SP_JSX.jsx(DFL.PanelSection, { children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => setView("main"), children: "\u2190 \u8FD4\u56DE" }) }) }), !available && (SP_JSX.jsx(DFL.PanelSection, { title: "\u63D0\u793A", children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: fanError ?? "该机型未提供可用的风扇控制节点" }) }) })), SP_JSX.jsxs(DFL.PanelSection, { title: "\u81EA\u5B9A\u4E49\u98CE\u6247\u66F2\u7EBF", children: [curve.map((point, index) => {
                            // 温度滑杆夹在左右邻居之间，滑不出重复或倒序；
                            // 这样"顺序错误"在动手时就不可达，保存时的校验只是兜底。
                            const bounds = tempBoundsFor(curve, index, minTemp, maxTemp);
                            return (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { width: "100%", paddingTop: "4px" }, children: [SP_JSX.jsxs("div", { style: rowStyle, children: [SP_JSX.jsxs("span", { children: ["\u8282\u70B9 ", index + 1, " \u00B7 ", point[0], "\u00B0C"] }), SP_JSX.jsxs("span", { children: ["PWM ", point[1]] })] }), SP_JSX.jsx(DFL.SliderField, { label: `温度（${bounds.min}–${bounds.max}°C）`, value: point[0], min: bounds.min, max: bounds.max, step: 1, showValue: false, disabled: fanBusy || !available || bounds.min > bounds.max, onChange: (value) => updateCurvePoint(index, 0, value) }), SP_JSX.jsx(DFL.SliderField, { label: "PWM", value: point[1], min: minPwm, max: maxPwm, step: 1, showValue: false, disabled: fanBusy || !available, onChange: (value) => updateCurvePoint(index, 1, value) })] }) }, index));
                        }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available || !curveDirty, onClick: saveCurve, children: "\u4FDD\u5B58\u4E3A\u81EA\u5B9A\u4E49\u5E76\u5E94\u7528" }) }), curveError && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: blockStyle, children: curveError }) }))] }), SP_JSX.jsxs(DFL.PanelSection, { title: "\u66F2\u7EBF\u9884\u8BBE", children: [presetButtons.map(([mode, label]) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available, onClick: () => loadPresetCurve(mode), children: ["\u5E94\u7528", label, "\u66F2\u7EBF"] }) }, mode))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy || !available || !curveDirty, onClick: restoreSeedCurve, children: "\u8FD8\u539F\u9ED8\u8BA4\u66F2\u7EBF" }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: "\u8BCA\u65AD", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: {
                                    ...blockStyle,
                                    fontFamily: "monospace",
                                    fontSize: "11px",
                                    opacity: 0.75,
                                }, children: ["\u63A7\u5236\u5668\uFF1A", fan?.controller ?? "--", SP_JSX.jsx("br", {}), "\u6E29\u5EA6\u4F20\u611F\u5668\uFF1A", fan?.temp_sensor ?? "--", SP_JSX.jsx("br", {}), "PWM \u8282\u70B9\uFF1A", fan?.pwm ?? "不存在", SP_JSX.jsx("br", {}), "pwm1_enable\uFF1A", fan?.pwm_enable ?? "不存在", fan?.pwm_enable_label ? `（${fan.pwm_enable_label}）` : "", SP_JSX.jsx("br", {}), "\u63A7\u5236\u65B9\u5F0F\uFF1A", fan?.manual ? "手动 PWM" : "EC 自动", SP_JSX.jsx("br", {}), "\u5B89\u5168\u9608\u503C\uFF1A", fan?.safety_temp ?? "--", "\u00B0C\uFF08\u8FBE\u5230\u5373\u6EE1\u901F\uFF09"] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy, onClick: refreshFan, children: "\u5237\u65B0" }) })] })] }));
    }
    /* -------------------------------------------------------------- 主页面 */
    if (error && !state) {
        return (SP_JSX.jsxs(DFL.PanelSection, { title: "F1Pro EC Control", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: error }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: refresh, children: "\u91CD\u65B0\u8BFB\u53D6" }) })] }));
    }
    const capacity = state?.capacity ?? "--";
    /** 风扇节点是否可用（不可用时整块降级成一行原因，不摆没用的按钮）。 */
    const fanAvailable = fan?.available ?? false;
    const acOnline = state?.ac_online ?? null;
    /**
     * 电池当前没有在充也没有在放：
     *
     * - 旁路模式（内核禁止充电）；
     * - 正常充电模式下内核报 `Not charging`，最常见的是**已达设定上限**。
     *
     * 这两种情况的物理状态完全一样——外接电源供整机、电池闲置，所以界面统一显示
     * 「供电中」，不区分原因（原因在下一行的「当前模式」里已经有了）。
     */
    const onAdapter = active === "inhibit-charge" ||
        active === "inhibit-charge-awake" ||
        state?.status === "Not charging";
    /** 外接电源在线；拿不到探测节点时（acOnline 为 null）退回看 status 是否在放电。 */
    const adapterLive = acOnline === true || (acOnline === null && state?.status !== "Discharging");
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
    const watts = !supplyOnly && flowing && typeof state?.power_watts === "number"
        ? `${state.power_watts >= 0 ? "" : "-"}${Math.abs(state.power_watts).toFixed(1)} W`
        : null;
    return (SP_JSX.jsxs("div", { ref: rootRef, children: [SP_JSX.jsxs(DFL.PanelSection, { title: "\u7535\u6C60\u72B6\u6001", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { display: "flex", justifyContent: "space-between", width: "100%" }, children: [SP_JSX.jsx("span", { children: state?.battery ?? "BAT" }), SP_JSX.jsxs("span", { children: [capacity, "%"] })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: [supplyOnly ? "供电中" : statusName(state?.status), watts ? ` · ${watts}` : "", " \u00B7 \u4E0A\u9650", " ", typeof thresholdValue === "number" ? `${thresholdValue}%` : "不限制", SP_JSX.jsx("br", {}), "\u5F53\u524D\u6A21\u5F0F\uFF1A", SP_JSX.jsx("b", { children: modeLabel(active) })] }) })] }), SP_JSX.jsxs(DFL.PanelSection, { title: "\u5145\u7535\u6A21\u5F0F", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_normal, onClick: () => changeMode("auto"), children: active === "auto" ? "✓ 正常充电" : "正常充电" }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_awake_bypass, onClick: () => changeMode("inhibit-charge-awake"), children: active === "inhibit-charge-awake" ? "✓ 开机旁路" : "开机旁路" }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy || !state?.supports_bypass, onClick: () => changeMode("inhibit-charge"), children: active === "inhibit-charge" ? "✓ 始终旁路" : "始终旁路" }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }, children: MODE_DESCRIPTIONS[active ?? ""] ?? "" }) })] }), SP_JSX.jsx(DFL.PanelSection, { title: "\u5145\u7535\u4E0A\u9650", children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.DropdownItem, { label: "\u505C\u6B62\u5145\u7535\u7535\u91CF", rgOptions: thresholdOptions, selectedOption: thresholdValue ?? state?.threshold_disabled_value ?? 100, disabled: busy || !state?.supports_threshold, onChange: (option) => changeThreshold(option.data) }) }) }), SP_JSX.jsx(DFL.PanelSection, { title: "\u98CE\u6247", children: fanAvailable ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: rowStyle, children: [SP_JSX.jsx("span", { children: fan?.temperature != null ? `${fan.temperature.toFixed(1)}°C` : "--" }), SP_JSX.jsx("span", { children: fan?.rpm != null ? `${fan.rpm} RPM` : "-- RPM" }), SP_JSX.jsx("span", { children: fan?.pwm != null ? `PWM ${fan.pwm}` : "PWM --" })] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: blockStyle, children: ["\u5F53\u524D\u6A21\u5F0F\uFF1A", SP_JSX.jsx("b", { children: FAN_MODE_LABELS[fan?.mode ?? ""] ?? fan?.mode_label ?? "未知" }), fan?.manual ? "（手动 PWM）" : "（EC 自动控温）"] }) }), FAN_MODE_ORDER.map((mode) => (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: fanBusy, onClick: () => changeFanMode(mode), children: fan?.mode === mode
                                    ? `✓ ${FAN_MODE_LABELS[mode]}`
                                    : FAN_MODE_LABELS[mode] }) }, mode))), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", onClick: () => setView("fan"), children: "\u7F16\u8F91\u98CE\u6247\u66F2\u7EBF \u2192" }) })] })) : (
                // 不可用时**只留一行原因**，不要摆一排点了没反应的按钮。
                SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { ...blockStyle, opacity: 0.8 }, children: fan?.reason ?? fanError ?? "未检测到 oxpec 风扇控制接口" }) })) }), SP_JSX.jsxs(DFL.PanelSection, { title: "\u8BBE\u7F6E", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ToggleField, { label: "Decky \u542F\u52A8\u65F6\u81EA\u52A8\u6062\u590D", description: "\u91CD\u65B0\u5E94\u7528\u4E0A\u6B21\u4FDD\u5B58\u7684\u5145\u7535\u6A21\u5F0F\u3001\u5145\u7535\u4E0A\u9650\u4E0E\u98CE\u6247\u6A21\u5F0F", checked: state?.auto_restore ?? true, disabled: busy, onChange: changeAutoRestore }) }), state?.restore_report && state.restore_report.length > 0 && (SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35, opacity: 0.8 }, children: state.restore_report.join("；") }) }))] }), error && (SP_JSX.jsx(DFL.PanelSection, { title: "\u63D0\u793A", children: SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx("div", { style: { whiteSpace: "normal", lineHeight: 1.35 }, children: error }) }) })), SP_JSX.jsxs(DFL.PanelSection, { title: "\u8BCA\u65AD", children: [SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsxs("div", { style: {
                                whiteSpace: "normal",
                                lineHeight: 1.35,
                                fontFamily: "monospace",
                                fontSize: "11px",
                                opacity: 0.75,
                            }, children: [state?.battery_path ?? "未找到电池节点", SP_JSX.jsx("br", {}), "\u5916\u63A5\u7535\u6E90: ", state?.ac_node ?? "无节点", acOnline === null ? "" : acOnline ? " 在线" : " 离线", SP_JSX.jsx("br", {}), "status: ", state?.status ?? "不存在", SP_JSX.jsx("br", {}), "charge_behaviour: ", state?.behaviour_raw ?? "不存在", SP_JSX.jsx("br", {}), "power_now: ", state?.power_now ?? "不存在", SP_JSX.jsx("br", {}), "threshold \u8282\u70B9: ", state?.threshold_node ? "存在" : "不存在", SP_JSX.jsx("br", {}), "\u98CE\u6247: ", fan?.controller ?? "未检测", fan?.available === false ? " · 不可用" : fan?.manual ? " · 手动" : " · EC 自动", state && !state.supports_awake_bypass ? (SP_JSX.jsxs(SP_JSX.Fragment, { children: [SP_JSX.jsx("br", {}), "\u5185\u6838\u672A\u63D0\u4F9B inhibit-charge-awake"] })) : null] }) }), SP_JSX.jsx(DFL.PanelSectionRow, { children: SP_JSX.jsx(DFL.ButtonItem, { layout: "below", disabled: busy, onClick: () => {
                                refresh();
                                refreshFan();
                            }, children: "\u5237\u65B0" }) })] })] }));
}
var index = definePlugin(() => ({
    name: "F1Pro EC Control",
    titleView: SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: "F1Pro EC Control" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx(BatteryIcon, {}),
}));

export { index as default };
//# sourceMappingURL=index.js.map
