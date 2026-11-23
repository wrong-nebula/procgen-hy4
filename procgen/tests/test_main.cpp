#include "testing.hpp"

#include <cstring>

int main(int argc, char** argv) {
    const char* filter = argc > 1 ? argv[1] : nullptr;
    int run = 0;
    for (const auto& c : pgtest::registry()) {
        if (filter && !std::strstr(c.name, filter)) continue;
        const int before = pgtest::failures();
        c.fn();
        ++run;
        std::printf("%s %s\n", pgtest::failures() == before ? "ok  " : "FAIL", c.name);
    }
    std::printf("%d tests, %d failed checks\n", run, pgtest::failures());
    return pgtest::failures() == 0 && run > 0 ? 0 : 1;
}
