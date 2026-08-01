/**
 * i18n type definitions for the Battle City Web multi-language subsystem.
 *
 * Design notes (MANIFEST §6 "Data over Code", §8 "Presentation Is Disposable"):
 * - Every user-visible string lives in a locale catalog (data), never in code.
 *   Adding a language = adding one `locales/<code>.ts` file + registering it
 *   in `index.ts`. No system code is touched.
 * - Language is purely presentation state. It is deliberately NOT stored on the
 *   World (MANIFEST §2.4): it never affects gameplay, so it must not pollute
 *   snapshots, replays, or the simulation. It lives here and persists to
 *   localStorage instead.
 */

/** A BCP-47-ish locale code. Extend by adding a union member + a catalog file. */
export type Locale = 'en' | 'zh'

/** A locale catalog is a flat map of dot-namespaced keys to translated strings.
 *  Interpolation placeholders use the `{name}` syntax (see `interpolate`). */
export type Catalog = Record<string, string>

/** Locale metadata: the code and the end-user-facing language name. */
export interface LocaleMeta {
  /** Locale code (must match a `Locale` union member). */
  code: Locale
  /** Native name shown in the LANGUAGE menu row, e.g. "English" / "中文". */
  name: string
}
