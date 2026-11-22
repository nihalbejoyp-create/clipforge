import type { Metadata, Viewport } from 'next';
import './globals.css';
import Shell from '@/components/Shell';

export const metadata: Metadata = {
  title: 'ClipForge — AI video & stream clipper',
  description: 'Local AI clipping for long videos and livestreams. No watermarks, no API keys.',
};

export const viewport: Viewport = {
  themeColor: '#07090d',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
