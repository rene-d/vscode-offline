# Visual Studio Code offline

Download Visuel Studio Code installers (ide, server, cli) and extensions.

## Mirror the apps

```shell
./vscode-offline.py
```

Download links are documented [here](https://code.visualstudio.com/docs/supporting/faq#_previous-release-versions).

## Download extensions

```shell
 ./vscode-offline.py -E -c files.in
```

Links are obtained via the (horrible) [ExtensionQuery](https://learn.microsoft.com/en-us/javascript/api/azure-devops-extension-api/extensionquery) interface.
