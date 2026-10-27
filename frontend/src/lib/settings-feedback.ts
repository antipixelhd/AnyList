import { showToast } from './toasts.ts';
import {markFailureHandled} from './handled-failures.ts';

export function escapeHtml(value: unknown): string {
  return value == null ? '' : String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function errorMessage(error: unknown): string {
  markFailureHandled(error);
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

export function showMessage(text: string, type: 'success' | 'error' = 'success'): void {
  showToast(text, type);
}
