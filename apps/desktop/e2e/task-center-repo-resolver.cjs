const fs = require('node:fs')
const path = require('node:path')

const candidateRoot = process.env.STARDUST_DESKTOP_HERMES_ROOT
const sharedVenvRoot = 'D:/Stardust/hermes-agent/.venv'

const sitePackages = path.join(sharedVenvRoot, 'Lib', 'site-packages')

if (candidateRoot && fs.existsSync(path.join(candidateRoot, 'hermes_cli', 'main.py'))) {
  if (!process.env.PYTHONPATH || !process.env.PYTHONPATH.split(path.delimiter).includes(candidateRoot)) {
    process.env.PYTHONPATH = [candidateRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  }

  if (!process.env.VIRTUAL_ENV || process.env.VIRTUAL_ENV === sharedVenvRoot) {
    process.env.VIRTUAL_ENV = sharedVenvRoot
    process.env.PYTHONPATH = [candidateRoot, sitePackages, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  }
}
