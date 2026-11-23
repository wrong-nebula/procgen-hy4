#pragma once
// Minimal dependency-free test harness (no network fetch needed to build).

#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

namespace pgtest {

struct Case { const char* name; std::function<void()> fn; };
inline std::vector<Case>& registry() { static std::vector<Case> r; return r; }
inline int& failures() { static int f = 0; return f; }

struct Registrar {
    Registrar(const char* name, std::function<void()> fn) { registry().push_back({name, std::move(fn)}); }
};

inline void fail(const char* file, int line, const std::string& msg) {
    std::fprintf(stderr, "  FAIL %s:%d: %s\n", file, line, msg.c_str());
    ++failures();
}

}  // namespace pgtest

#define PG_CAT2(a, b) a##b
#define PG_CAT(a, b) PG_CAT2(a, b)
#define TEST(name)                                                             \
    static void name();                                                        \
    static ::pgtest::Registrar PG_CAT(reg_, name)(#name, &name);               \
    static void name()

#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) ::pgtest::fail(__FILE__, __LINE__, "CHECK(" #cond ")");   \
    } while (0)

#define CHECK_EQ(a, b)                                                         \
    do {                                                                       \
        const auto pg_a = (a);                                                 \
        const auto pg_b = (b);                                                 \
        if (!(pg_a == pg_b))                                                   \
            ::pgtest::fail(__FILE__, __LINE__,                                 \
                           std::string("CHECK_EQ(" #a ", " #b "): ") +         \
                               std::to_string(pg_a) + " != " + std::to_string(pg_b)); \
    } while (0)

#define CHECK_NEAR(a, b, tol)                                                  \
    do {                                                                       \
        const double pg_a = (a);                                               \
        const double pg_b = (b);                                               \
        if (!(std::abs(pg_a - pg_b) <= (tol)))                                 \
            ::pgtest::fail(__FILE__, __LINE__,                                 \
                           std::string("CHECK_NEAR(" #a ", " #b "): ") +       \
                               std::to_string(pg_a) + " vs " + std::to_string(pg_b)); \
    } while (0)

#define CHECK_THROWS(expr)                                                     \
    do {                                                                       \
        bool pg_threw = false;                                                 \
        try { (void)(expr); } catch (...) { pg_threw = true; }                 \
        if (!pg_threw) ::pgtest::fail(__FILE__, __LINE__, "expected throw: " #expr); \
    } while (0)
