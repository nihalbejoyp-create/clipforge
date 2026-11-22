'use client';

import { useEffect, useState } from 'react';

export type ToastTone = 'info' | 'success' | 'error';
export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

let items: ToastItem[] = [];
let listeners: ((list: ToastItem[]) => void)[] = [];
let counter = 0;

function emit() {
  listeners.forEach((l) => l([...items]));
}

export function toast(message: string, tone: ToastTone = 'info') {
  const item: ToastItem = { id: ++counter, message, tone };
  items = [...items, item];
  emit();
  setTimeout(() => {
    items = items.filter((t) => t.id !== item.id);
    emit();
  }, tone === 'error' ? 6500 : 4000);
}

export function useToasts() {
  const [list, setList] = useState<ToastItem[]>(items);
  useEffect(() => {
    const fn = (l: ToastItem[]) => setList(l);
    listeners.push(fn);
    return () => {
      listeners = listeners.filter((x) => x !== fn);
    };
  }, []);
  return list;
}
