import Foundation

// Standalone contract tests; compile alongside HerdrGlassBackdrop.swift.
@main
struct GlassLayoutTests {
    static func main() throws {
        func decode(_ json: String) throws -> HerdrGlassLayout {
            try JSONDecoder().decode(HerdrGlassLayout.self, from: Data(json.utf8))
        }
        let valid = #"{"version":1,"cols":120,"rows":40,"regions":[{"kind":"tab","x":24,"y":0,"width":30,"height":1,"selected":true}]}"#
        let layout = try decode(valid)
        precondition(layout.isValid)
        precondition(layout.regions[0].selected)
        for (old, replacement) in [
            ("\"version\":1", "\"version\":2"),
            ("\"x\":24", "\"x\":-1"),
            ("\"width\":30", "\"width\":120"),
            ("\"width\":30", "\"width\":9223372036854775807"),
            ("\"kind\":\"tab\"", "\"kind\":\"unknown\"")
        ] {
            let invalid = try decode(valid.replacingOccurrences(of: old, with: replacement))
            precondition(!invalid.isValid)
        }
        print("Glass layout contract: 7 checks passed")
    }
}
