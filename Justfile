config := if path_exists("config.in") == "true" { "config.in" } else { "default.in" }

featured:
    python3 vscode.py --version 1.96.4 -c {{ config }} --prune

latest:
    python3 vscode.py -c {{ config }} --prune

clean:
    rm -f query_*.json response_*.json