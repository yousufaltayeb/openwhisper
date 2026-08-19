export const HELP = `OpenWhisper — local-first Arabic-English dictation

Usage:
  openwhisper [ui]
  openwhisper record start|stop|toggle|cancel|status [--wait]
  openwhisper transcribe <path|-> [--mode raw|clean|code] [--insert]
  openwhisper history list|search|show|copy|delete|clear|export
  openwhisper modes list|show|select
  openwhisper vocab list|add|remove|import|export
  openwhisper snippets list|add|remove|run|import|export
  openwhisper models list|install|remove|verify|select|import
  openwhisper providers list|configure|test|unset
  openwhisper config list|get|set
  openwhisper service install|start|stop|restart|status|uninstall
  openwhisper setup
  openwhisper doctor
  openwhisper logs
  openwhisper completion [bash|zsh|fish|powershell]
  openwhisper update
  openwhisper version

Global output options:
  --plain       Stable, unstyled text
  --json        One JSON document
  --jsonl       One JSON value per line
  --no-color    Disable color (also honored through NO_COLOR)
  --no-start    Never start the daemon automatically

Exit codes: 0 success, 2 usage/configuration, 3 daemon unavailable,
4 unsupported/permission, 5 model/provider unavailable, 6 transcription/cleanup,
7 insertion, 8 network/I/O, 130 cancellation.
`;

export function completion(shell: string): string {
  const commands = "ui record transcribe history modes vocab snippets models providers config service setup doctor logs completion update version";
  if (shell === "fish") return `complete -c openwhisper -f -a '${commands}'\n`;
  if (shell === "zsh") return `#compdef openwhisper\n_arguments '1:command:(${commands})'\n`;
  if (shell === "powershell") return `Register-ArgumentCompleter -Native -CommandName openwhisper -ScriptBlock { '${commands}'.Split(' ') }\n`;
  return `complete -W '${commands}' openwhisper\n`;
}
