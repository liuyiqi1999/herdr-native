import AppKit
import Darwin

// Finder launches this executable directly. No shell startup or shell quoting.
let bundle = Bundle.main
let fm = FileManager.default
func fail(_ message: String) -> Never {
    let alert = NSAlert()
    alert.messageText = "Herdr Native could not start"
    alert.informativeText = message
    alert.runModal()
    exit(1)
}
guard let resources = bundle.resourceURL else { fail("Missing application resources") }
let support = fm.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/Herdr Native", isDirectory: true)
let config = ProcessInfo.processInfo.environment["HERDR_NATIVE_CONFIG_PATH"]
    .map { URL(fileURLWithPath: $0) } ?? support.appendingPathComponent("herdr.toml")
let session = ProcessInfo.processInfo.environment["HERDR_NATIVE_SESSION"] ?? "native"
do {
    try fm.createDirectory(at: support, withIntermediateDirectories: true)
    if !fm.fileExists(atPath: config.path) {
        try fm.copyItem(at: resources.appendingPathComponent("herdr.toml"), to: config)
    }
    let originalText = try String(contentsOf: config, encoding: .utf8)
    var text = originalText
    if !text.contains("herdr-native:new-agent") {
        let addition = "\n[[keys.command]]\nkey = \"super+shift+a\"\ncommand = \"herdr-native:new-agent\"\ntype = \"shell\"\ndescription = \"New remote agent\"\n"
        text += addition
    }
    if text != originalText {
        try text.write(to: config, atomically: true, encoding: .utf8)
    }
} catch { fail(error.localizedDescription) }
let bin = bundle.bundleURL.appendingPathComponent("Contents/MacOS")
setenv("HERDR_CONFIG_PATH", config.path, 1)
setenv("HERDR_SESSION", session, 1)
// Ignore obsolete customization flags inherited from an older application.
for key in ["HERDR_NATIVE_GROUP_ROOT", "HERDR_NATIVE_GROUP_ID", "HERDR_NATIVE_GROUP_LAUNCHER",
            "HERDR_NATIVE_GLASS", "HERDR_NATIVE_GLASS_ROOT", "HERDR_NATIVE_SURFACE_UUID"] {
    unsetenv(key)
}
setenv("HERDR_NATIVE_APP", bundle.bundlePath, 1)
setenv("HERDR_REMOTE_LAUNCHER", bin.appendingPathComponent("herdr-remote").path, 1)
// This launcher creates an independent desktop client, even when invoked
// from a Herdr pane. Do not inherit the invoking pane's nesting/identity or
// socket routing. Keep nesting protection enabled inside actual pane shells.
for key in ["HERDR_ENV", "HERDR_PANE_ID", "HERDR_PANE_RUNTIME_ID",
            "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HERDR_ACTIVE_PANE_ID",
            "HERDR_ACTIVE_PANE_CWD", "HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH"] {
    unsetenv(key)
}
let home = fm.homeDirectoryForCurrentUser.path
let paths = [bin.path, "\(home)/.local/bin", "\(home)/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
let inherited = ProcessInfo.processInfo.environment["PATH"] ?? ""
setenv("PATH", (paths + [inherited]).joined(separator: ":"), 1)
setenv("GHOSTTY_RESOURCES_DIR", resources.appendingPathComponent("ghostty").path, 1)
let engine = bin.appendingPathComponent("ghostty").path
// Keep the child command inside a single option. AppKit can interpret bare
// existing paths after `-e` as open-file requests and create a second surface.
// The bundle's MacOS directory is first in PATH, so this resolves to our Herdr
// even when the application path contains spaces, without invoking a shell.
let arguments = [engine, "--config-default-files=false",
                 "--config-file=\(resources.appendingPathComponent("ghostty.conf").path)",
                 "--working-directory=\(home)",
                 "--initial-command=direct:herdr --session \(session)"]
let pointers = arguments.map { strdup($0) } + [nil]
pointers.withUnsafeBufferPointer { buffer in
    _ = execv(engine, buffer.baseAddress!)
}
fail("Unable to launch Ghostty: \(String(cString: strerror(errno)))")
