const verifiedRuntime = "../../../.devspace/worktrees/alexandria-audiobook.git-65a3bc77"

const runtimeEnvironment = {
  ALEXANDRIA_RUNTIME_PYTHON: "{{path.resolve(cwd, 'app/env/bin/python')}}",
  ALEXANDRIA_CONFIG_PATH: "{{path.resolve(cwd, 'config.json')}}",
  ALEXANDRIA_LEGACY_ROOT_DIR: "{{path.resolve(cwd)}}",
}

module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        shell: "{{which('bash')}}",
        env: runtimeEnvironment,
        path: ".",
        message: `node "{{path.resolve(cwd, '${verifiedRuntime}/preflight.js')}}"`
      }
    },
    {
      method: "shell.run",
      params: {
        shell: "{{which('bash')}}",
        env: runtimeEnvironment,
        path: ".",
        message: `node "{{path.resolve(cwd, '${verifiedRuntime}/backend_runtime.js')}}" --host 127.0.0.1 --port 4200 --python "{{path.resolve(cwd, 'app/env/bin/python')}}" --app "{{path.resolve(cwd, '${verifiedRuntime}/app/app.py')}}"`,
        on: [{
          event: "/Alexandria backend ready: (http:\\/\\/[0-9.]+:[0-9]+\\/)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        backend_url: "{{input.event[1]}}",
        url: "{{input.event[1] + '?pinokio_reload=' + input.id}}"
      }
    }
  ]
}
