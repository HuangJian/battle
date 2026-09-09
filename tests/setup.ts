/**
 * Test preload (bunfig.toml `[test] preload`): register happy-dom's browser
 * globals so UI integration tests run against a real DOM emulator instead of
 * the hand-rolled fake (tests/helpers/fake-dom.ts, now deleted — see
 * DECISIONS §2026-09-08-ps2-happydom).
 *
 * Global registration is deliberate: UIManager/Game construct the UI through
 * bare `document.createElement`/`document.body` calls, so a per-file Window
 * instance would require re-wiring every global by hand. Headless logic tests
 * never touch the DOM, so the registered globals are inert for them.
 *
 * HAZARD (2p-review R2-P2-4): the preload registers localStorage/navigator
 * for EVERY test file, so the shared store is visible project-wide. No test
 * currently calls loadSettings/persistSettings directly (grep-verified), but
 * any FUTURE settings test must NOT rely on the ambient localStorage — pass
 * its own storage (or clear it) so it cannot read/write another test's data.
 */
import { GlobalRegistrator } from '@happy-dom/global-registrator'

GlobalRegistrator.register()
