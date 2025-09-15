config := if path_exists("config.in") == "true" { "config.in" } else { "example.conf" }

version := "1.100.3"

featured:
    python3 vscode-offline.py --version {{ version }} -c {{ config }} --prune

latest:
    python3 vscode-offline.py -c {{ config }} --prune

clean:
    rm -f query_*.json response_*.json