import type { SavedView, Theme } from "./types";

const SAVED_VIEWS_KEY = "code-view.saved-views.v1";
const BOOKMARKS_KEY = "code-view.bookmarks.v1";
const THEME_KEY = "code-view.theme.v1";

const read = <T,>(key: string, fallback: T): T => {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
};

const write = (key: string, value: unknown) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Local persistence is optional; private browser sessions may reject it.
  }
};

export const loadSavedViews = () => read<SavedView[]>(SAVED_VIEWS_KEY, []);
export const storeSavedViews = (views: SavedView[]) => write(SAVED_VIEWS_KEY, views);
export const loadBookmarks = () => read<string[]>(BOOKMARKS_KEY, []);
export const storeBookmarks = (ids: string[]) => write(BOOKMARKS_KEY, ids);
export const resolveTheme = (stored: unknown, prefersLight: boolean): Theme => stored === "light" || stored === "dark" ? stored : prefersLight ? "light" : "dark";
export const loadTheme = () => resolveTheme(read<unknown>(THEME_KEY, null), typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches);
export const storeTheme = (theme: Theme) => write(THEME_KEY, theme);
