#pragma once
// Minimal dependency-free JSON: parse into an ordered tree, write with a
// streaming writer. Enough for geometry documents, receipts and manifests.

#include <cstddef>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace pg::json {

struct Member;

struct Value {
    enum class Kind : unsigned char { Null, Bool, Number, String, Array, Object };
    Kind kind = Kind::Null;
    bool boolean = false;
    double number = 0.0;
    std::string string;
    std::vector<Value> array;
    std::vector<Member> object;  // insertion order preserved

    [[nodiscard]] bool is_null() const noexcept { return kind == Kind::Null; }
    [[nodiscard]] bool is_number() const noexcept { return kind == Kind::Number; }
    [[nodiscard]] bool is_string() const noexcept { return kind == Kind::String; }
    [[nodiscard]] bool is_array() const noexcept { return kind == Kind::Array; }
    [[nodiscard]] bool is_object() const noexcept { return kind == Kind::Object; }
    // Object member lookup; nullptr when absent or not an object.
    [[nodiscard]] const Value* find(std::string_view key) const noexcept;
};

struct Member {
    std::string key;
    Value value;
};

// Throws std::runtime_error("json: <what> at byte N") on malformed input.
[[nodiscard]] Value parse(std::string_view text);
[[nodiscard]] Value parse_file(const std::string& path);

// Streaming writer producing compact, valid JSON. Commas are automatic.
class Writer {
public:
    Writer& begin_object();
    Writer& end_object();
    Writer& begin_array();
    Writer& end_array();
    Writer& key(std::string_view k);
    Writer& value(std::string_view s);
    Writer& value(const char* s) { return value(std::string_view(s)); }
    Writer& value(double d);
    Writer& value(bool b);
    Writer& value(std::size_t n);
    Writer& value(long long n);
    Writer& value(int n) { return value(static_cast<long long>(n)); }
    Writer& null();
    Writer& raw(std::string_view json);  // pre-serialized fragment
    template <class T>
    Writer& field(std::string_view k, const T& v) { key(k); return value(v); }
    [[nodiscard]] const std::string& str() const noexcept { return out_; }

private:
    void separate();
    std::string out_;
    std::vector<bool> first_{true};
    bool after_key_ = false;
};

}  // namespace pg::json
