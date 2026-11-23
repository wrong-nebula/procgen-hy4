#include "pg/json.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace pg::json {

const Value* Value::find(std::string_view key) const noexcept {
    if (kind != Kind::Object) return nullptr;
    for (const Member& m : object)
        if (m.key == key) return &m.value;
    return nullptr;
}

namespace {

struct Parser {
    std::string_view s;
    std::size_t i = 0;
    int depth = 0;

    [[noreturn]] void fail(const char* what) const {
        throw std::runtime_error(std::string("json: ") + what + " at byte " + std::to_string(i));
    }
    void ws() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\t' || s[i] == '\r')) ++i;
    }
    bool eat(char c) {
        ws();
        if (i < s.size() && s[i] == c) { ++i; return true; }
        return false;
    }
    void expect(std::string_view word) {
        if (s.substr(i, word.size()) != word) fail("unexpected token");
        i += word.size();
    }
    static void utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out += static_cast<char>(cp);
        else if (cp < 0x800) { out += static_cast<char>(0xC0 | (cp >> 6)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }
    unsigned hex4() {
        if (i + 4 > s.size()) fail("truncated \\u escape");
        unsigned v = 0;
        for (int k = 0; k < 4; ++k) {
            const char c = s[i++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= static_cast<unsigned>(c - '0');
            else if (c >= 'a' && c <= 'f') v |= static_cast<unsigned>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= static_cast<unsigned>(c - 'A' + 10);
            else fail("bad \\u escape");
        }
        return v;
    }
    std::string string() {
        std::string out;
        for (;;) {
            if (i >= s.size()) fail("unterminated string");
            const char c = s[i++];
            if (c == '"') return out;
            if (c != '\\') { out += c; continue; }
            if (i >= s.size()) fail("unterminated escape");
            const char e = s[i++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    if (cp >= 0xD800 && cp < 0xDC00 && s.substr(i, 2) == "\\u") {
                        i += 2;
                        const unsigned lo = hex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    utf8(out, cp);
                    break;
                }
                default: fail("bad escape");
            }
        }
    }
    Value value() {
        if (++depth > 200) fail("nesting too deep");
        ws();
        if (i >= s.size()) fail("unexpected end");
        Value v;
        const char c = s[i];
        if (c == '{') {
            ++i;
            v.kind = Value::Kind::Object;
            if (!eat('}')) {
                do {
                    ws();
                    if (i >= s.size() || s[i] != '"') fail("expected object key");
                    ++i;
                    Member m;
                    m.key = string();
                    if (!eat(':')) fail("expected ':'");
                    m.value = value();
                    v.object.push_back(std::move(m));
                } while (eat(','));
                if (!eat('}')) fail("expected '}'");
            }
        } else if (c == '[') {
            ++i;
            v.kind = Value::Kind::Array;
            if (!eat(']')) {
                do v.array.push_back(value());
                while (eat(','));
                if (!eat(']')) fail("expected ']'");
            }
        } else if (c == '"') {
            ++i;
            v.kind = Value::Kind::String;
            v.string = string();
        } else if (c == 't') { expect("true"); v.kind = Value::Kind::Bool; v.boolean = true; }
        else if (c == 'f') { expect("false"); v.kind = Value::Kind::Bool; }
        else if (c == 'n') { expect("null"); }
        else {
            const std::string buf(s.substr(i, 64));
            char* end = nullptr;
            const double d = std::strtod(buf.c_str(), &end);
            if (end == buf.c_str()) fail("unexpected character");
            i += static_cast<std::size_t>(end - buf.c_str());
            v.kind = Value::Kind::Number;
            v.number = d;
        }
        --depth;
        return v;
    }
};

}  // namespace

Value parse(std::string_view text) {
    Parser p{text};
    Value v = p.value();
    p.ws();
    if (p.i != text.size()) p.fail("trailing characters");
    return v;
}

Value parse_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("json: cannot open " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return parse(ss.str());
}

void Writer::separate() {
    if (after_key_) { after_key_ = false; return; }
    if (!first_.back()) out_ += ',';
    first_.back() = false;
}
Writer& Writer::begin_object() { separate(); out_ += '{'; first_.push_back(true); return *this; }
Writer& Writer::end_object() { out_ += '}'; first_.pop_back(); return *this; }
Writer& Writer::begin_array() { separate(); out_ += '['; first_.push_back(true); return *this; }
Writer& Writer::end_array() { out_ += ']'; first_.pop_back(); return *this; }
namespace {
void quote(std::string& out, std::string_view s) {
    out += '"';
    for (const char ch : s) {
        const auto c = static_cast<unsigned char>(ch);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); out += b; }
                else out += ch;
        }
    }
    out += '"';
}
}  // namespace

Writer& Writer::key(std::string_view k) {
    separate();
    quote(out_, k);
    out_ += ':';
    after_key_ = true;
    return *this;
}
Writer& Writer::value(std::string_view s) {
    separate();
    quote(out_, s);
    return *this;
}
Writer& Writer::value(double d) {
    separate();
    if (!std::isfinite(d)) { out_ += "null"; return *this; }
    char b[40];
    std::snprintf(b, sizeof b, "%.9g", d);
    out_ += b;
    return *this;
}
Writer& Writer::value(bool b) { separate(); out_ += b ? "true" : "false"; return *this; }
Writer& Writer::value(std::size_t n) { separate(); out_ += std::to_string(n); return *this; }
Writer& Writer::value(long long n) { separate(); out_ += std::to_string(n); return *this; }
Writer& Writer::null() { separate(); out_ += "null"; return *this; }
Writer& Writer::raw(std::string_view json) { separate(); out_ += json; return *this; }

}  // namespace pg::json
