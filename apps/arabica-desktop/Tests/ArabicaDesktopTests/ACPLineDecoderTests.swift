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

    func testACPRequestIDParsing() {
        let intID = ACPRequestID(from: 42)
        XCTAssertEqual(intID, .int(42))
        XCTAssertEqual(intID?.description, "42")
        XCTAssertEqual(intID?.jsonValue as? Int, 42)

        let stringID = ACPRequestID(from: "uuid-1234-5678")
        XCTAssertEqual(stringID, .string("uuid-1234-5678"))
        XCTAssertEqual(stringID?.description, "uuid-1234-5678")
        XCTAssertEqual(stringID?.jsonValue as? String, "uuid-1234-5678")

        let nsNumID = ACPRequestID(from: NSNumber(value: 100))
        XCTAssertEqual(nsNumID, .int(100))

        XCTAssertNil(ACPRequestID(from: ["bad": true]))
    }
}
