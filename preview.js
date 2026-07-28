module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "node tests/reference_fidelity_preview.js --upstream http://127.0.0.1:4200 --host 127.0.0.1 --port 0",
        on: [{
          event: "/(http:\\/\\/[0-9.:]+)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        url: "{{input.event[1]}}"
      }
    }
  ]
}
