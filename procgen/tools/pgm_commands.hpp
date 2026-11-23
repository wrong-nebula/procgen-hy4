#pragma once
// Command table for pgm. Each command writes exactly one JSON object.

#include "pg/doc.hpp"
#include "pg/json.hpp"

#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace pgm {

// Positional arguments plus --name value options; --set name=value repeats.
class Args {
public:
    explicit Args(const std::vector<std::string>& raw) {
        for (std::size_t i = 0; i < raw.size(); ++i) {
            const std::string& a = raw[i];
            if (a.rfind("--", 0) != 0) { positional.push_back(a); continue; }
            const std::string name = a.substr(2);
            if (name == "set") {
                if (i + 1 >= raw.size()) throw std::runtime_error("--set needs name=value");
                const std::string& kv = raw[++i];
                const std::size_t eq = kv.find('=');
                if (eq == std::string::npos || eq == 0 || eq + 1 >= kv.size()) throw std::runtime_error("--set needs name=value");
                // Built from explicit ranges: GCC 12 misjudges substr() here (-Wstringop-overflow).
                const std::string key(kv.begin(), kv.begin() + static_cast<std::ptrdiff_t>(eq));
                controls[key] = std::stof(kv.c_str() + eq + 1);
            } else if (flag_names().count(name)) {
                options[name] = "1";
            } else {
                if (i + 1 >= raw.size()) throw std::runtime_error("--" + name + " needs a value");
                options[name] = raw[++i];
            }
        }
    }
    [[nodiscard]] const std::string& at(std::size_t i, const char* what) const {
        if (i >= positional.size()) throw std::runtime_error(std::string("missing argument: ") + what);
        return positional[i];
    }
    [[nodiscard]] std::string get(const std::string& name, const std::string& fallback = "") const {
        const auto it = options.find(name);
        return it == options.end() ? fallback : it->second;
    }
    [[nodiscard]] bool has(const std::string& name) const { return options.count(name) != 0; }

    std::vector<std::string> positional;
    std::map<std::string, std::string> options;
    pg::doc::Controls controls;

private:
    static const std::map<std::string, int>& flag_names() {
        static const std::map<std::string, int> f = {
            {"intersections", 1}, {"no-dedupe", 1}, {"json", 1}, {"zup", 1},
            {"sweep", 1}, {"dangling", 1}, {"efficiency", 1}, {"stdin", 1}, {"once", 1},
            {"frame-strict", 1}, {"no-details", 1}, {"allow-open", 1}, {"wire-cage", 1}, {"no-meta", 1}, {"audit", 1}
        };
        return f;
    }
};

struct Command {
    const char* name;
    const char* usage;
    const char* summary;
    int (*run)(const Args&, pg::json::Writer&);
};

const std::vector<Command>& commands();

}  // namespace pgm
