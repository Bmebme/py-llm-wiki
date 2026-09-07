/**
 * Browser shim for `@tauri-apps/plugin-dialog`.
 *
 * Native file/folder dialogs don't exist in the browser; M4 replaces
 * the call sites with <input type="file"> / download flows. Until then
 * open()/save() return null (callers already handle null) and message()
 * falls back to alert/confirm. The overloads mirror the desktop
 * plugin's typing so call sites typecheck unchanged.
 */

export interface DialogFilter {
  name: string
  extensions: string[]
}

export interface OpenDialogOptions {
  title?: string
  directory?: boolean
  multiple?: boolean
  defaultPath?: string
  filters?: DialogFilter[]
  createDirectories?: boolean
}

export interface SaveDialogOptions {
  title?: string
  defaultPath?: string
  filters?: DialogFilter[]
}

export interface MessageDialogOptions {
  title?: string
  kind?: "info" | "warning" | "error"
  okLabel?: string
}

export async function open(
  options: OpenDialogOptions & { multiple: true },
): Promise<string[] | null>
export async function open(
  options?: OpenDialogOptions & { multiple?: false },
): Promise<string | null>
export async function open(
  options?: OpenDialogOptions,
): Promise<string | string[] | null> {
  // 浏览器无原生目录选择器: directory 模式降级为路径输入框,
  // 否则"打开项目"按钮因 open() 恒返 null 而无反应 (内网实调)。
  if (options?.directory) {
    const title = options.title ?? "打开项目"
    const value = window.prompt(
      `${title}: 请输入项目路径 (容器视角绝对路径, 如 /projects/mae)`,
      options.defaultPath ?? "",
    )
    if (!value || !value.trim()) return null
    return options.multiple ? [value.trim()] : value.trim()
  }
  // 文件选择器: M4 由 <input type="file"> 流程替换, 暂返回 null
  return null
}

export async function save(_options?: SaveDialogOptions): Promise<string | null> {
  return null
}

export async function message(text: string, options?: MessageDialogOptions): Promise<void> {
  if (options?.kind === "error") {
    // eslint-disable-next-line no-alert
    alert(text)
  }
}

export async function confirm(text: string, _options?: MessageDialogOptions): Promise<boolean> {
  // eslint-disable-next-line no-alert
  return window.confirm(text)
}

export async function ask(text: string, _options?: MessageDialogOptions): Promise<boolean> {
  // eslint-disable-next-line no-alert
  return window.confirm(text)
}
