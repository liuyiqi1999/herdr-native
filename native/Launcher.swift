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
let config = support.appendingPathComponent("herdr.toml")
do {
    try fm.createDirectory(at: support, withIntermediateDirectories: true)
    if !fm.fileExists(atPath: config.path) {
        try fm.copyItem(at: resources.appendingPathComponent("herdr.toml"), to: config)
    }
    let originalText = try String(contentsOf: config, encoding: .utf8)
    var text = originalText
    if text.range(of: #"(?m)^\s*prompt_new_tab_command\s*="#, options: .regularExpression) == nil {
        if let section = text.range(of: #"(?m)^\[ui\][ \t]*(?:#.*)?(?:\r?\n|$)"#, options: .regularExpression) {
            text.insert(contentsOf: "\nprompt_new_tab_command = true\n", at: section.upperBound)
        } else {
            text += "\n[ui]\nprompt_new_tab_command = true\n"
        }
    }
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
setenv("HERDR_REMOTE_LAUNCHER", bin.appendingPathComponent("herdr-remote").path, 1)
// Do not accidentally attach to an inherited CLI session.
unsetenv("HERDR_SOCKET_PATH")
unsetenv("HERDR_CLIENT_SOCKET_PATH")
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
                 "--initial-command=direct:herdr --session native"]
let pointers = arguments.map { strdup($0) } + [nil]
pointers.withUnsafeBufferPointer { buffer in
    _ = execv(engine, buffer.baseAddress!)
}
fail("Unable to launch Ghostty: \(String(cString: strerror(errno)))")
