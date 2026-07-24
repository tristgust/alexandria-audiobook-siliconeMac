module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "node preflight.js"
      }
    }
  ]
}
