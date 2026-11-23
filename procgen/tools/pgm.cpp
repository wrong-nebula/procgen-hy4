// pgm — the procgen modeler's command line. Every command prints one JSON
// object on stdout (errors too: {"passed":false,"error":...}, exit code 1), so
// it can be driven by scripts, agents and the MCP server (tools/pgm_mcp.py)
// without parsing prose.
//
//   pgm evaluate    <doc.json> [--set name=value]...          counts and bounds
//   pgm inspect     <doc.json> [--set ...]                     controls, slots, modules, ports
//   pgm check       <doc.json> [--set ...] [--intersections]   mesh quality per prototype and placed
//   pgm catalog     <directory>                                documents and modules
//   pgm conformance <directory> <receipts.json>                compare against reference receipts
//   pgm export      <doc.json> --emesh out.emesh [--obj out.obj] [--no-dedupe]
//   pgm render      <doc.json|pack.emesh> --out file.png [--view iso|opposite|front|side|top]
//                   [--size WxH] [--pass beauty|clay|parts|materials] [--backend software|engine]
//   pgm sheet       <sheet.json> --out file.png                multi-panel kit sheet
#include "pgm_commands.hpp"

#include "pg/doc.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>

int main(int argc, char** argv) {
    const std::vector<std::string> args(argv + 1, argv + argc);
    pg::json::Writer w;
    int code = 0;
    try {
        // PGM_PROJECTION_SEARCH=scan|index forces how details find the surface. The output is the
        // same either way; the switch is for checking exactly that.
        if (const char* search = std::getenv("PGM_PROJECTION_SEARCH")) {
            if (!std::strcmp(search, "scan")) pg::doc::set_projection_search(pg::doc::ProjectionSearch::Scan);
            else if (!std::strcmp(search, "index")) pg::doc::set_projection_search(pg::doc::ProjectionSearch::Index);
            else if (std::strcmp(search, "auto") != 0) throw std::runtime_error("PGM_PROJECTION_SEARCH must be auto, scan or index");
        }
        // `pgm <command> --help` (or -h) prints that command's usage instead of failing on a missing option value.
        bool wants_help = false;
        for (const std::string& a : args) wants_help = wants_help || a == "--help" || a == "-h";
        const pgm::Command* asked = nullptr;
        if (wants_help && !args.empty())
            for (const pgm::Command& c : pgm::commands())
                if (args[0] == c.name) asked = &c;
        if (asked) {
            w.begin_object().field("passed", true).field("name", asked->name).field("usage", asked->usage)
                .field("summary", asked->summary).end_object();
        } else if (args.empty() || args[0] == "help" || wants_help) {
            w.begin_object().field("passed", true).key("commands").begin_array();
            for (const pgm::Command& c : pgm::commands()) {
                w.begin_object().field("name", c.name).field("usage", c.usage).field("summary", c.summary).end_object();
            }
            w.end_array().end_object();
        } else {
            const pgm::Command* cmd = nullptr;
            for (const pgm::Command& c : pgm::commands())
                if (args[0] == c.name) cmd = &c;
            if (!cmd) {
                std::string message = "unknown command '";  // built with += : GCC 12 misreads the operator+ chain
                message += args[0];
                message += "'; run pgm help";
                throw std::runtime_error(message);
            }
            code = cmd->run(pgm::Args(std::vector<std::string>(args.begin() + 1, args.end())), w);
        }
    } catch (const std::exception& e) {
        w = pg::json::Writer();
        w.begin_object().field("passed", false).field("error", e.what()).end_object();
        code = 1;
    }
    std::printf("%s\n", w.str().c_str());
    return code;
}
