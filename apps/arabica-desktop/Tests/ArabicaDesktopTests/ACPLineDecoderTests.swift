import Foundation
import XCTest
@testable import ArabicaDesktop

final class ACPLineDecoderTests: XCTestCase {
    func testSplitAndConsecutiveMessages() {
        var decoder = ACPLineDecoder()
        XCTAssertTrue(decoder.append(Data(#"{"jsonrpc":"2.0","id":1,"result":{}"#.utf8)).isEmpty)
        let messages = decoder.append(Data("}\n{\"jsonrpc\":\"2.0\",\"method\":\"session/update\"}\n".utf8))
        XCTAssertEqual(messages.count, 2)
        XCTAssertEqual(messages[0]["id"] as? Int, 1)
        XCTAssertEqual(messages[1]["method"] as? String, "session/update")
    }
}
