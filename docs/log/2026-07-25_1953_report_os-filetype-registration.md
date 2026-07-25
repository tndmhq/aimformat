---
date: 2026-07-25 19:53
type: report
status: done
related: []
---

# Making a bare .aim render in the browser (OS file-type registration, verified)

[`spec.md`](../../spec.md) §10 lists three bridges for the fact that browsers
key **local** files off the extension: a helper serving `Content-Type:
text/html`, OS file-type registration, or the `.aim.html` alias. The middle one
was untested. It works; this entry records how, and the limit found.

## What was measured (macOS 15, Chrome, aimformat 0.3.0)

Before registering anything, macOS maps `.aim` to a *dynamic* UTI with no MIME
type at all:

```
UTI: dyn.ah62d4rv4ge80c4pr
preferredMIMEType: nil
conformsTo html: false
```

Chrome resolves a `file://` MIME type from its own table first and falls
through to the OS type database for extensions it does not know. With no MIME
type coming back, it renders the document as plain text.

Registering a UTI fixes exactly that. A stub `.app` whose `Info.plist`
declares:

```xml
<key>UTExportedTypeDeclarations</key>
<array><dict>
  <key>UTTypeIdentifier</key><string>com.tndm.aim</string>
  <key>UTTypeConformsTo</key><array><string>public.html</string></array>
  <key>UTTypeTagSpecification</key><dict>
    <key>public.filename-extension</key><array><string>aim</string></array>
    <key>public.mime-type</key><array><string>text/html</string></array>
  </dict>
</dict></array>
```

registered with `lsregister -f <app>` yields:

```
UTI: com.tndm.aim
preferredMIMEType: text/html
conformsTo html: true
```

Verified with a control, using headless Chrome's `--dump-dom` on the same
bytes under two extensions:

- `.zzq` (unregistered): DOM is `<html><head>…</head><body><pre>` wrapping the
  escaped source. Plain text.
- `.aim` (registered): DOM is the parsed document — `<!DOCTYPE html><html
  data-aim-version="0.3">…`, with all `aim-proposal` elements present as
  nodes.

So a bare `.aim` does render natively once the OS knows the type.

## The limit: rendering and default-app are two different things

The UTI declaration governs how an application that opens the file
*interprets* it. It does not decide *which* application opens it on
double-click; that is the Launch Services handler ranking, driven by an app's
`CFBundleDocumentTypes` plus the user's own "Open With" choice. Observed
after registration: double-click still went to a text editor, while
right-click → Open With → Chrome rendered the document correctly.

Making double-click land in a browser needs an installed application that
claims the type as a handler (`CFBundleDocumentTypes` with an
`LSHandlerRank`), or a one-time user choice of default app. That is an
application-install concern, not a format concern.

## Portability

The same idea exists on the other platforms and is unverified here:

- Linux: an XDG shared-mime-info package under `~/.local/share/mime`
  declaring `.aim` as `text/html`, then `update-mime-database`.
- Windows: `HKCR\.aim\Content Type = text/html`.
- Served over HTTP nothing is needed beyond the server mapping already in
  §10; the response header always wins over the extension.

## Consequences

- §10's "OS file-type registration" bridge is real, with the caveat above
  worth stating: it fixes rendering, not the default-app association.
- A packaged application (or an opt-in CLI verb) could register the type on
  install. Not proposed here — it is a maintainer decision, and it introduces
  an install-time side effect on the user's system that the current
  zero-dependency, no-install-footprint tooling deliberately avoids.
- The `.aim.html` alias remains the portable answer when a file must open
  by double-click on a machine that has never installed anything, which is
  why the spec keeps it as a compatibility alias and not the canonical name.

The test bundle used for this measurement was unregistered and deleted
afterwards; nothing in the repo depends on it.
