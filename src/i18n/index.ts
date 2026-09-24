/**
 * 插件自己的 i18n 实现。
 *
 * **为什么不直接用 Decky 的机制**：Decky 对"插件"没有官方 i18n ——
 * 本地 `@decky/ui@4.12.1` 的类型定义里**没有任何** translation/i18n 导出
 * （Loader 自身用的是 react-i18next，但那是它自己界面的，插件用不上）。
 * 社区通行做法就是各插件自己写一份，这里也一样。
 *
 * 语言探测用 `window.LocalizationManager.m_rgLocalesToUse` —— 它是 Steam
 * 客户端暴露的语言列表（如 `["schinese"]`），并且是 `@decky/ui` **官方声明**
 * 在全局类型里的字段（`@decky/ui/dist/globals/stores.d.ts`），
 * 比 `SteamClient.Settings.GetCurrentLanguage()`（返回 Promise，只能异步取）
 * 更适合在渲染路径上同步调用。
 *
 * 规则（用户定的）：**简/繁中文 → 中文，其余一律英文**。
 *
 * 探测不到时**退回英文**，并且**不抛错** —— 最坏情况是界面变英文，
 * 不该因为 Steam 内部结构变了就让整个 QAM 面板空白。
 */
import { MESSAGE_KEYS, format, type MessageKey } from "./keys";
import { zhCN } from "./zh-CN";
import { enUS } from "./en-US";

export type { MessageKey };
export { MESSAGE_KEYS, format };

/** 判定为"中文"的 Steam 语言标识（小写比较）。 */
const CHINESE_LOCALES = [
  "schinese", // 简体中文
  "tchinese", // 繁体中文
  "zh", // 通用中文
  "zh-cn",
  "zh-hans",
  "zh-tw",
  "zh-hant",
  "zh-hk",
];

/**
 * 从 Steam 的语言标识列表里挑出插件要用的语言。
 *
 * `locales` 形如 `["schinese"]`；空数组 / 取不到 → 英文。
 * 只要**任一**个标识是中文就判为中文：Steam 给的是"按优先级排序的列表"，
 * 用户选了中文时它一定在列表里。
 */
export function resolveLanguage(locales: readonly string[] | null | undefined): "zh" | "en" {
  if (!locales || locales.length === 0) return "en";
  for (const locale of locales) {
    if (typeof locale !== "string") continue;
    if (CHINESE_LOCALES.includes(locale.trim().toLowerCase())) return "zh";
  }
  return "en";
}

/** 同步读 Steam 的界面语言列表；任何异常都退回空列表。 */
export function detectLocales(): string[] {
  try {
    const manager = window.LocalizationManager;
    const locales = manager?.m_rgLocalesToUse;
    if (Array.isArray(locales)) return locales.filter((x) => typeof x === "string");
  } catch {
    // 访问 Steam 全局对象在非 Steam 环境（如离线测试）会抛错，
    // 这里静默退回，交给上面的"英文兜底"。
  }
  return [];
}

const TABLES = { zh: zhCN, en: enUS } as const;

/**
 * 按当前语言取文案。
 *
 * 找不到键时**返回键本身**（而不是空串）—— 空串会让界面出现"什么都没有"
 * 的诡异空白，返回键至少能一眼看出是漏了哪条。
 */
export function translate(
  lang: "zh" | "en",
  key: MessageKey,
  values?: Record<string, string | number>,
): string {
  const table = TABLES[lang] ?? enUS;
  const template = table[key];
  if (typeof template !== "string") return key;
  return values ? format(template, values) : template;
}

/** 一次解析出当前语言与取文案函数，供组件在渲染前调用一次。 */
export function createTranslator(locales?: readonly string[]) {
  const lang = resolveLanguage(locales ?? detectLocales());
  return {
    lang,
    t: (key: MessageKey, values?: Record<string, string | number>) =>
      translate(lang, key, values),
  };
}
