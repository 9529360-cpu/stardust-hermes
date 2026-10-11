import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.stardustDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('stardust:translucency:support')
const hudWindowing = ipcRenderer.sendSync('stardust:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true
const launchFlags = ipcRenderer.sendSync('stardust:launch-flags')

contextBridge.exposeInMainWorld('stardustDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  // Launch-flag fact: the app was started with --local, so the renderer may
  // show the local-models surfaces. Static for the window's lifetime.
  localModelsEnabled: launchFlags?.localModels === true,
  // Launch-flag fact: the Nous free tier is on for this launch
  // (STARDUST_GUEST_ONBOARDING=1 or --guest-onboarding). Read-only; the same
  // decision is stamped onto every backend the app spawns.
  guestOnboardingEnabled: launchFlags?.guestOnboarding === true,
  // Launch-flag fact: skip the first-run film (STARDUST_SKIP_INTRO=1 or
  // --skip-intro). Rehearsal aid for the guided chat behind it.
  skipIntro: launchFlags?.skipIntro === true,
  getConnection: (profile, opts) => ipcRenderer.invoke('stardust:connection', profile, opts),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('stardust:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('stardust:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('stardust:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('stardust:backend:touch', profile),
  getPoolLimits: () => ipcRenderer.invoke('stardust:pool-limits:get'),
  setPoolLimits: limits => ipcRenderer.invoke('stardust:pool-limits:set', limits),
  getGatewayWsUrl: profile => ipcRenderer.invoke('stardust:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('stardust:gateway:ws-url-for', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('stardust:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('stardust:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('stardust:window:openInTerminal', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('stardust:window:openInstance'),
  openBrowserWindow: tabId => ipcRenderer.invoke('stardust:window:openBrowser', tabId),
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('stardust:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('stardust:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('stardust:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('stardust:wake-indicator:get'),
    setState: state => ipcRenderer.send('stardust:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('stardust:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('stardust:wake-indicator:state', listener)
    }
  },
  chatOnboarding: {
    grow: request => ipcRenderer.send('stardust:chat-onboarding:grow', request),
    soloBoot: () => ipcRenderer.send('stardust:chat-onboarding:solo-boot')
  },
  introReveal: {
    open: (payload?: { hideMain?: boolean }) => ipcRenderer.invoke('stardust:intro-reveal:open', payload),
    close: (payload?: { showMain?: boolean }) => ipcRenderer.invoke('stardust:intro-reveal:close', payload),
    skip: () => ipcRenderer.send('stardust:intro-reveal:skip'),
    ready: () => ipcRenderer.send('stardust:intro-reveal:ready'),
    onSkip: callback => {
      const listener = () => callback()

      ipcRenderer.on('stardust:intro-reveal:skip', listener)

      return () => ipcRenderer.removeListener('stardust:intro-reveal:skip', listener)
    },
    onClosed: callback => {
      const listener = () => callback()

      ipcRenderer.on('stardust:intro-reveal:closed', listener)

      return () => ipcRenderer.removeListener('stardust:intro-reveal:closed', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('stardust:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('stardust:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('stardust:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('stardust:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('stardust:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('stardust:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('stardust:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('stardust:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('stardust:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('stardust:hud:open', request),
    close: () => ipcRenderer.invoke('stardust:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('stardust:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('stardust:hud:begin-move'),
    endMove: () => ipcRenderer.send('stardust:hud:end-move'),
    moveBy: delta => ipcRenderer.send('stardust:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('stardust:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('stardust:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('stardust:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('stardust:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('stardust:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('stardust:hud:goto', listener)

      return () => ipcRenderer.removeListener('stardust:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('stardust:hud:changed', listener)

      return () => ipcRenderer.removeListener('stardust:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('stardust:hud:cursor', listener)

      return () => ipcRenderer.removeListener('stardust:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('stardust:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('stardust:hud:game-overlay', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('stardust:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('stardust:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('stardust:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('stardust:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('stardust:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('stardust:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('stardust:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('stardust:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('stardust:quick-entry:shown', listener)
    }
  },
  browserControl: {
    start: (payload: { profile?: string | null; launchContext: object; chromeProfileDir: string; packageSpec: string }) => ipcRenderer.invoke('stardust:browser-control:bridge:start', payload),
    stop: () => ipcRenderer.invoke('stardust:browser-control:bridge:stop'),
    status: () => ipcRenderer.invoke('stardust:browser-control:bridge:status'),
    onStatus: callback => {
      const listener = (_event, status) => callback(status)
      ipcRenderer.on('stardust:browser-control:bridge:status', listener)
      return () => ipcRenderer.removeListener('stardust:browser-control:bridge:status', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('stardust:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('stardust:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('stardust:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('stardust:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('stardust:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('stardust:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('stardust:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('stardust:connections:list'),
    save: payload => ipcRenderer.invoke('stardust:connections:save', payload),
    remove: id => ipcRenderer.invoke('stardust:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('stardust:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('stardust:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('stardust:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('stardust:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('stardust:connections:update-managed', id),
    // Fan out `hermes update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('stardust:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:connections:changed', listener)

      return () => ipcRenderer.removeListener('stardust:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('stardust:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('stardust:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('stardust:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('stardust:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('stardust:connection-config:oauth-logout', remoteUrl),
  profile: {
    get: () => ipcRenderer.invoke('stardust:profile:get'),
    remember: name => ipcRenderer.invoke('stardust:profile:remember', name),
    set: name => ipcRenderer.invoke('stardust:profile:set', name)
  },
  api: request => ipcRenderer.invoke('stardust:api', request),
  notify: payload => ipcRenderer.invoke('stardust:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('stardust:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('stardust:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('stardust:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('stardust:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('stardust:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('stardust:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('stardust:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('stardust:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('stardust:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('stardust:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('stardust:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('stardust:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('stardust:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('stardust:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('stardust:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('stardust:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('stardust:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('stardust:context-menu:guest-add-word', payload),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('stardust:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext, name) => ipcRenderer.invoke('stardust:saveImageBuffer', { data, ext, name }),
  capturePreview: payload => ipcRenderer.invoke('stardust:capturePreview', payload),
  savePastedText: text => ipcRenderer.invoke('stardust:savePastedText', { text }),
  saveClipboardImage: () => ipcRenderer.invoke('stardust:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('stardust:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('stardust:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('stardust:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('stardust:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('stardust:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('stardust:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('stardust:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('stardust:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('stardust:keep-awake', on),
  setDisableF12: blocked => ipcRenderer.send('stardust:devtools:disable-f12', blocked),
  setPreviewShortcutActive: active => ipcRenderer.send('stardust:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('stardust:openExternal', url),
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('stardust:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('stardust:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('stardust:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('stardust:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('stardust:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('stardust:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('stardust:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('stardust:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('stardust:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('stardust:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('stardust:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('stardust:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('stardust:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('stardust:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:zoom:changed', listener)

      return () => ipcRenderer.removeListener('stardust:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('stardust:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('stardust:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('stardust:logs:renderer-error', report),
  readDir: dirPath => ipcRenderer.invoke('stardust:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('stardust:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('stardust:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('stardust:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('stardust:fs:desktopPluginsRoot'),
  reconcileDesktopPlugins: () => ipcRenderer.invoke('stardust:fs:reconcileDesktopPlugins'),
  logsRoot: () => ipcRenderer.invoke('stardust:fs:logsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('stardust:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('stardust:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('stardust:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('stardust:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('stardust:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('stardust:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('stardust:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('stardust:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('stardust:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('stardust:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('stardust:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('stardust:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('stardust:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('stardust:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('stardust:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('stardust:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('stardust:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('stardust:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('stardust:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('stardust:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('stardust:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('stardust:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('stardust:git:review:prList', repoPath, branches, numbers),
      fetchPrComment: (repoPath, url) => ipcRenderer.invoke('stardust:git:review:fetchPrComment', repoPath, url),
      createPr: repoPath => ipcRenderer.invoke('stardust:git:review:createPr', repoPath)
    }
  },
  terminal: {
    attach: id => ipcRenderer.invoke('stardust:terminal:attach', id),
    cwd: id => ipcRenderer.invoke('stardust:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('stardust:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('stardust:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('stardust:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('stardust:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `stardust:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `stardust:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('stardust:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('stardust:preview-nav', listener)

    return () => ipcRenderer.removeListener('stardust:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('stardust:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:open-updates', listener)

    return () => ipcRenderer.removeListener('stardust:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:deep-link', listener)

    return () => ipcRenderer.removeListener('stardust:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('stardust:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('stardust:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('stardust:plugin:installDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:window-state-changed', listener)

    return () => ipcRenderer.removeListener('stardust:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('stardust:focus-session', listener)

    return () => ipcRenderer.removeListener('stardust:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:notification-action', listener)

    return () => ipcRenderer.removeListener('stardust:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:notification-activate', listener)

    return () => ipcRenderer.removeListener('stardust:notification-activate', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('stardust:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:backend-exit', listener)

    return () => ipcRenderer.removeListener('stardust:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:connection:applied', listener)

    return () => ipcRenderer.removeListener('stardust:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:power-resume', listener)

    return () => ipcRenderer.removeListener('stardust:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('stardust:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('stardust:power-battery', listener)

    return () => ipcRenderer.removeListener('stardust:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:boot-progress', listener)

    return () => ipcRenderer.removeListener('stardust:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('stardust:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('stardust:bootstrap:continue-local'),
  recycleBackend: profile => ipcRenderer.invoke('stardust:backend:recycle', profile),
  resetBootstrap: () => ipcRenderer.invoke('stardust:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('stardust:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('stardust:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('stardust:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('stardust:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('stardust:version'),
  relaunchApp: () => ipcRenderer.invoke('stardust:app:relaunch'),
  getMachineProfile: () => ipcRenderer.invoke('stardust:machine:profile'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('stardust:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('stardust:uninstall:summary'),
    run: mode => ipcRenderer.invoke('stardust:uninstall:run', { mode })
  },
  updates: {
    check: opts => ipcRenderer.invoke('stardust:updates:check', opts),
    apply: opts => ipcRenderer.invoke('stardust:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('stardust:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('stardust:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('stardust:updates:progress', listener)

      return () => ipcRenderer.removeListener('stardust:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('stardust:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('stardust:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('stardust:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('stardust:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('stardust:found-in-page', listener)

    return () => ipcRenderer.removeListener('stardust:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('stardust:open-find-bar', listener)

    return () => ipcRenderer.removeListener('stardust:open-find-bar', listener)
  }
})
