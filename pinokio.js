module.exports = {
  version: "5.0",
  title: "Alexandria",
  description: "Create and produce audiobooks with a stable working interface and a separate read-only preview of the rebuilt interface.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      preview: info.running("preview.js"),
      reset: info.running("reset.js"),
      update: info.running("update.js"),
      validate: info.running("validate.js"),
    }
    const ready = {
      start: info.ready("start.js"),
      preview: info.ready("preview.js"),
    }
    const installed = info.exists("app/env")

    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Installing",
        href: "install.js",
      }]
    }
    if (running.reset) {
      return [{
        default: true,
        icon: "fa-solid fa-rotate-left",
        text: "Resetting",
        href: "reset.js",
      }]
    }
    if (running.update) {
      return [{
        default: true,
        icon: "fa-solid fa-arrows-rotate",
        text: "Updating",
        href: "update.js",
      }]
    }
    if (running.validate) {
      return [{
        default: true,
        icon: "fa-solid fa-shield-halved",
        text: "Validating Builds",
        href: "validate.js",
      }]
    }

    if (running.start || running.preview) {
      const startLocal = info.local("start.js")
      const previewLocal = info.local("preview.js")
      const menu = []

      if (running.start && ready.start && startLocal && startLocal.url) {
        menu.push({
          default: true,
          icon: "fa-solid fa-book-open",
          text: "Open Stable Build (Old UI)",
          href: startLocal.url,
        })
      } else if (running.start) {
        menu.push({
          default: true,
          icon: "fa-solid fa-terminal",
          text: "Starting Stable Build",
          href: "start.js",
        })
      } else {
        menu.push({
          default: true,
          icon: "fa-solid fa-power-off",
          text: "Start Stable Build",
          href: "start.js",
        })
      }

      if (running.preview && ready.preview && previewLocal && previewLocal.url) {
        menu.push({
          icon: "fa-solid fa-flask",
          text: "Open New UI Preview (Read-only)",
          href: previewLocal.url,
        })
      } else if (running.preview) {
        menu.push({
          icon: "fa-solid fa-terminal",
          text: "Starting New UI Preview",
          href: "preview.js",
        })
      } else {
        menu.push({
          icon: "fa-solid fa-flask",
          text: "Start New UI Preview",
          href: "preview.js",
        })
      }

      if (running.start) {
        menu.push({
          icon: "fa-solid fa-terminal",
          text: "Stable Build Terminal",
          href: "start.js",
        })
      }
      if (running.preview) {
        menu.push({
          icon: "fa-solid fa-terminal",
          text: "Preview Terminal",
          href: "preview.js",
        })
      }
      return menu
    }

    if (!installed) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Install",
        href: "install.js",
      }]
    }

    return [{
      default: true,
      icon: "fa-solid fa-layer-group",
      text: "Start Stable + New Preview",
      href: "preview.js",
    }, {
      icon: "fa-solid fa-book-open",
      text: "Start Stable Build Only",
      href: "start.js",
    }, {
      icon: "fa-solid fa-shield-halved",
      text: "Validate Builds",
      href: "validate.js",
    }, {
      icon: "fa-solid fa-folder-open",
      text: "Open Voicelines",
      href: "voicelines",
    }, {
      icon: "fa-solid fa-arrows-rotate",
      text: "Update",
      href: "update.js",
    }, {
      icon: "fa-solid fa-plug",
      text: "Reinstall",
      href: "install.js",
    }, {
      icon: "fa-solid fa-rotate-left",
      text: "Reset",
      href: "reset.js",
    }]
  },
}
