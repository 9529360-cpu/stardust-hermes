export type RadixAppearance = 'dark' | 'light'
export type RadixSemanticColor = 'amber' | 'blue' | 'gray' | 'green' | 'red' | 'violet'

const RADIX_SCALES = {
  gray: {
    light: ['#fcfcfc', '#f9f9f9', '#f0f0f0', '#e8e8e8', '#e0e0e0', '#d9d9d9', '#cecece', '#bbbbbb', '#8d8d8d', '#838383', '#646464', '#202020'],
    dark: ['#111111', '#191919', '#222222', '#2a2a2a', '#313131', '#3a3a3a', '#484848', '#606060', '#6e6e6e', '#7b7b7b', '#b4b4b4', '#eeeeee']
  },
  blue: {
    light: ['#fbfdff', '#f4faff', '#e6f4fe', '#d5efff', '#c2e5ff', '#acd8fc', '#8ec8f6', '#5eb1ef', '#0090ff', '#0588f0', '#0d74ce', '#113264'],
    dark: ['#0d1520', '#111927', '#0d2847', '#003362', '#004074', '#104d87', '#205d9e', '#2870bd', '#0090ff', '#3b9eff', '#70b8ff', '#c2e6ff']
  },
  red: {
    light: ['#fffcfc', '#fff7f7', '#feebec', '#ffdbdc', '#ffcdce', '#fdbdbe', '#f4a9aa', '#eb8e90', '#e5484d', '#dc3e42', '#ce2c31', '#641723'],
    dark: ['#191111', '#201314', '#3b1219', '#500f1c', '#611623', '#72232d', '#8c333a', '#b54548', '#e5484d', '#ec5d5e', '#ff9592', '#ffd1d9']
  },
  green: {
    light: ['#fbfefc', '#f4fbf6', '#e6f6eb', '#d6f1df', '#c4e8d1', '#adddc0', '#8eceaa', '#5bb98b', '#30a46c', '#2b9a66', '#218358', '#193b2d'],
    dark: ['#0e1512', '#121b17', '#132d21', '#113b29', '#174933', '#20573e', '#28684a', '#2f7c57', '#30a46c', '#33b074', '#3dd68c', '#b1f1cb']
  },
  amber: {
    light: ['#fefdfb', '#fefbe9', '#fff7c2', '#ffee9c', '#fbe577', '#f3d673', '#e9c162', '#e2a336', '#ffc53d', '#ffba18', '#ab6400', '#4f3422'],
    dark: ['#16120c', '#1d180f', '#302008', '#3f2700', '#4d3000', '#5c3d05', '#714f19', '#8f6424', '#ffc53d', '#ffd60a', '#ffca16', '#ffe7b3']
  },
  violet: {
    light: ['#fdfcfe', '#faf8ff', '#f4f0fe', '#ebe4ff', '#e1d9ff', '#d4cafe', '#c2b5f5', '#aa99ec', '#6e56cf', '#654dc4', '#6550b9', '#2f265f'],
    dark: ['#14121f', '#1b1525', '#291f43', '#33255b', '#3c2e69', '#473876', '#56468b', '#6958ad', '#6e56cf', '#7d66d9', '#b1a4ff', '#e2ddfe']
  }
} as const satisfies Record<RadixSemanticColor, Record<RadixAppearance, readonly string[]>>

const SEMANTIC_SCALES: Readonly<Record<RadixSemanticColor, RadixSemanticColor>> = {
  amber: 'amber',
  blue: 'blue',
  gray: 'gray',
  green: 'green',
  red: 'red',
  violet: 'violet'
}

function radixValues(appearance: RadixAppearance): Record<string, string> {
  const values: Record<string, string> = {}

  for (const semantic of Object.keys(SEMANTIC_SCALES) as RadixSemanticColor[]) {
    const scale = RADIX_SCALES[SEMANTIC_SCALES[semantic]][appearance]
    scale.forEach((value, index) => {
      values[`--ui-${semantic}-scale-${index + 1}`] = value
      values[`--ui-${semantic}-${index + 1}`] = value
    })
    values[`--ui-${semantic}-text`] = scale[10]
    values[`--ui-${semantic}`] = scale[10]
    values[`--ui-bg-${semantic}`] = scale[2]
    values[`--ui-solid-${semantic}`] = scale[8]
    values[`--ui-solid-hover-${semantic}`] = scale[9]
    values[`--ui-border-${semantic}`] = scale[6]
    values[`--ui-ring-${semantic}`] = scale[7]
  }

  return values
}

const RADIX_DEFAULT_OVERRIDES: Record<RadixAppearance, Record<string, string>> = {
  light: {
    ...radixValues('light'),
    '--theme-foreground': '#202020',
    '--theme-primary': '#0d74ce',
    '--theme-secondary': '#f4faff',
    '--theme-accent-soft': '#e6f4fe',
    '--theme-midground': '#0d74ce',
    '--theme-warm': '#ab6400',
    '--theme-background-seed': '#fcfcfc',
    '--theme-sidebar-seed': '#f9f9f9',
    '--theme-card-seed': '#fcfcfc',
    '--theme-elevated-seed': '#fcfcfc',
    '--theme-bubble-seed': '#e6f4fe',
    '--theme-neutral-chrome': '#f9f9f9',
    '--theme-neutral-sidebar': '#fcfcfc',
    '--radius-scalar': '0.4',
    '--theme-neutral-card': '#fcfcfc',
    '--ui-bg-card': '#f0f0f0',
    '--ui-bg-input': '#fcfcfc',
    '--ui-bg-primary': '#e6f4fe',
    '--ui-bg-secondary': '#f4faff',
    '--ui-bg-tertiary': '#f0f0f0',
    '--ui-bg-quaternary': '#e8e8e8',
    '--ui-bg-quinary': '#f9f9f9',
    '--ui-row-hover-background': '#f0f0f0',
    '--ui-row-active-background': '#e6f4fe',
    '--ui-control-hover-background': '#f0f0f0',
    '--ui-control-active-background': '#e6f4fe',
    '--ui-text-primary': '#202020',
    '--ui-text-secondary': '#646464',
    '--ui-text-tertiary': '#646464',
    '--ui-text-quaternary': '#646464',
    '--ui-stroke-primary': '#bbbbbb',
    '--ui-stroke-secondary': '#cecece',
    '--ui-stroke-tertiary': '#d9d9d9',
    '--ui-stroke-quaternary': '#d9d9d9',
    '--ui-stroke-width': '0.5px',
    '--ui-sash-hover-border': '#838383',
    '--ui-sash-hover-background': '#e6f4fe',
    '--ui-inline-code-background': '#f0f0f0',
    '--ui-inline-code-foreground': '#202020',
    '--ui-selection-background': '#c2e5ff',
    '--dt-primary-foreground': '#113264',
    '--dt-primary-solid': '#0d74ce',
    '--dt-primary-solid-foreground': '#113264',
    '--dt-primary': '#0d74ce',
    '--dt-secondary': '#f4faff',
    '--dt-accent': '#e6f4fe',
    '--dt-muted': '#f0f0f0',
    '--dt-destructive': '#ce2c31',
    '--ui-destructive-text': '#ce2c31',
    '--ui-red-text': '#ce2c31',
    '--ui-green-text': '#218358',
    '--ui-amber-text': '#ab6400',
    '--ui-blue-text': '#0d74ce',
    '--ui-violet-text': '#6e56cf',
    '--ui-red-solid': '#e5484d',
    '--ui-green-solid': '#30a46c',
    '--ui-amber-solid': '#e2a336',
    '--ui-blue-solid': '#5eb1ef',
    '--ui-violet-solid': '#aa99ec',
    '--dt-destructive-foreground': '#ffffff',
    '--dt-sidebar-border': '#d9d9d9',
    '--dt-user-bubble-border': '#d5efff',
    '--dt-border': '#d9d9d9',
    '--dt-input': '#bbbbbb',
    '--dt-ring': '#5eb1ef',
    '--dt-composer-ring': '#0d74ce',
    '--dt-midground': '#0d74ce',
    '--dt-sidebar-bg': '#f9f9f9',
    '--ui-red': '#ce2c31',
    '--ui-orange': '#ab6400',
    '--ui-yellow': '#ab6400',
    '--ui-green': '#218358',
    '--ui-cyan': '#0d74ce',
    '--ui-blue': '#0d74ce',
    '--ui-purple': '#6e56cf',
    '--ui-success': '#218358',
    '--ui-diff-add-background': '#e6f6eb',
    '--ui-diff-remove-background': '#feebec',
    '--ui-diff-add-border': '#218358',
    '--ui-diff-add-foreground': '#218358',
    '--ui-diff-remove-border': '#ce2c31',
    '--ui-diff-remove-foreground': '#ce2c31',
    '--context-usage-tools': '#6e56cf',
    '--context-usage-rules': '#218358',
    '--context-usage-skills': '#ab6400',
    '--context-usage-mcp': '#ce2c31',
    '--context-usage-subagents': '#0d74ce',
    '--context-usage-memory': '#ab6400',
    '--context-usage-conversation': '#0d74ce'
  },
  dark: {
    ...radixValues('dark'),
    '--theme-foreground': '#eeeeee',
    '--theme-primary': '#0090ff',
    '--theme-secondary': '#111927',
    '--theme-accent-soft': '#0d2847',
    '--theme-midground': '#70b8ff',
    '--theme-warm': '#ffca16',
    '--theme-background-seed': '#111111',
    '--theme-sidebar-seed': '#191919',
    '--theme-card-seed': '#191919',
    '--theme-elevated-seed': '#222222',
    '--theme-bubble-seed': '#0d2847',
    '--theme-neutral-chrome': '#191919',
    '--theme-neutral-sidebar': '#111111',
    '--radius-scalar': '0.4',
    '--theme-neutral-card': '#191919',
    '--ui-bg-card': '#222222',
    '--ui-bg-input': '#191919',
    '--ui-bg-primary': '#0d2847',
    '--ui-bg-secondary': '#111927',
    '--ui-bg-tertiary': '#222222',
    '--ui-bg-quaternary': '#2a2a2a',
    '--ui-bg-quinary': '#191919',
    '--ui-row-hover-background': '#222222',
    '--ui-row-active-background': '#0d2847',
    '--ui-control-hover-background': '#222222',
    '--ui-control-active-background': '#0d2847',
    '--ui-text-primary': '#eeeeee',
    '--ui-text-secondary': '#b4b4b4',
    '--ui-text-tertiary': '#b4b4b4',
    '--ui-text-quaternary': '#b4b4b4',
    '--ui-stroke-primary': '#7b7b7b',
    '--ui-stroke-secondary': '#606060',
    '--ui-stroke-tertiary': '#484848',
    '--ui-stroke-quaternary': '#484848',
    '--ui-stroke-width': '0.5px',
    '--ui-sash-hover-border': '#b4b4b4',
    '--ui-sash-hover-background': '#0d2847',
    '--ui-inline-code-background': '#2a2a2a',
    '--ui-inline-code-foreground': '#eeeeee',
    '--ui-selection-background': '#205d9e',
    '--dt-primary-foreground': '#0d1520',
    '--dt-primary-solid': '#0090ff',
    '--dt-primary-solid-foreground': '#0d1520',
    '--dt-primary': '#0090ff',
    '--dt-secondary': '#111927',
    '--dt-accent': '#0d2847',
    '--dt-muted': '#222222',
    '--dt-destructive': '#ff9592',
    '--ui-destructive-text': '#ff9592',
    '--ui-red-text': '#ff9592',
    '--ui-green-text': '#3dd68c',
    '--ui-amber-text': '#ffca16',
    '--ui-blue-text': '#70b8ff',
    '--ui-violet-text': '#b1a4ff',
    '--ui-red-solid': '#b54548',
    '--ui-green-solid': '#2f7c57',
    '--ui-amber-solid': '#8f6424',
    '--ui-blue-solid': '#2870bd',
    '--ui-violet-solid': '#6958ad',
    '--dt-destructive-foreground': '#191111',
    '--dt-sidebar-border': '#484848',
    '--dt-user-bubble-border': '#003362',
    '--dt-border': '#484848',
    '--dt-input': '#606060',
    '--dt-ring': '#2870bd',
    '--dt-composer-ring': '#70b8ff',
    '--dt-midground': '#70b8ff',
    '--dt-sidebar-bg': '#191919',
    '--ui-red': '#ff9592',
    '--ui-orange': '#ffca16',
    '--ui-yellow': '#ffca16',
    '--ui-green': '#3dd68c',
    '--ui-cyan': '#70b8ff',
    '--ui-blue': '#70b8ff',
    '--ui-purple': '#b1a4ff',
    '--ui-success': '#3dd68c',
    '--ui-diff-add-background': '#132d21',
    '--ui-diff-remove-background': '#3b1219',
    '--ui-diff-add-border': '#3dd68c',
    '--ui-diff-add-foreground': '#3dd68c',
    '--ui-diff-remove-border': '#ff9592',
    '--ui-diff-remove-foreground': '#ff9592',
    '--context-usage-tools': '#b1a4ff',
    '--context-usage-rules': '#3dd68c',
    '--context-usage-skills': '#ffca16',
    '--context-usage-mcp': '#ff9592',
    '--context-usage-subagents': '#70b8ff',
    '--context-usage-memory': '#ffca16',
    '--context-usage-conversation': '#70b8ff'
  }
}

export function radixDefaultThemeOverrides(appearance: RadixAppearance): Record<string, string> {
  return RADIX_DEFAULT_OVERRIDES[appearance]
}
