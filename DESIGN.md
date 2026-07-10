---
name: Lumina
colors:
  surface: '#fbf8ff'
  surface-dim: '#dad9e3'
  surface-bright: '#fbf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f4f2fd'
  surface-container: '#eeedf7'
  surface-container-high: '#e8e7f1'
  surface-container-highest: '#e3e1ec'
  on-surface: '#1a1b22'
  on-surface-variant: '#4c4546'
  inverse-surface: '#2f3038'
  inverse-on-surface: '#f1effa'
  outline: '#7e7576'
  outline-variant: '#cfc4c5'
  surface-tint: '#5e5e5e'
  primary: '#000000'
  on-primary: '#ffffff'
  primary-container: '#1b1b1b'
  on-primary-container: '#848484'
  inverse-primary: '#c6c6c6'
  secondary: '#4b41e1'
  on-secondary: '#ffffff'
  secondary-container: '#645efb'
  on-secondary-container: '#fffbff'
  tertiary: '#000000'
  on-tertiary: '#ffffff'
  tertiary-container: '#191c1d'
  on-tertiary-container: '#828485'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e2e2e2'
  primary-fixed-dim: '#c6c6c6'
  on-primary-fixed: '#1b1b1b'
  on-primary-fixed-variant: '#474747'
  secondary-fixed: '#e2dfff'
  secondary-fixed-dim: '#c3c0ff'
  on-secondary-fixed: '#0f0069'
  on-secondary-fixed-variant: '#3323cc'
  tertiary-fixed: '#e1e3e4'
  tertiary-fixed-dim: '#c5c7c8'
  on-tertiary-fixed: '#191c1d'
  on-tertiary-fixed-variant: '#454748'
  background: '#fbf8ff'
  on-background: '#1a1b22'
  surface-variant: '#e3e1ec'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '600'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 8px
  container-max: 1440px
  gutter: 24px
  margin-desktop: 48px
  margin-mobile: 16px
  masonry-gap: 16px
---

## Brand & Style

The design system is built for a high-end photo management and AI-driven curation platform. The brand personality is sophisticated, observant, and silent, allowing the photography to remain the primary focus. 

The aesthetic blends **Minimalism** with **Modern Enterprise** sensibilities. It utilizes expansive whitespace to create "breathing room" between assets, ensuring the UI never competes with the visual content. High-contrast elements are used sparingly to guide the user's eye toward AI-powered features and navigation landmarks. The emotional response should be one of professional-grade organization and effortless discovery.

## Colors

The palette is strictly neutral to maintain color accuracy for the photography. 
- **Primary:** Pure black (#000000) is used for typography, borders, and primary actions to establish high contrast against the white canvas.
- **Secondary (AI Accent):** A vibrant Electric Indigo (#4F46E5) is reserved exclusively for AI-driven features, search suggestions, and semantic "magic" interactions.
- **Surface Tiers:** Uses a series of cool grays. The background is pure white (#FFFFFF), with secondary containers using a subtle off-white (#F9FAFB) to define UI boundaries without heavy lines.
- **Feedback:** Success, error, and warning states should use desaturated versions of standard semantical colors to avoid clashing with the photos.

## Typography

This design system utilizes **Inter** for all primary communication, selected for its neutrality and exceptional legibility at variable scales. To emphasize the technical, AI-driven nature of the product, **JetBrains Mono** is introduced for metadata, labels, and technical specs (ISO, aperture, file names).

Headlines use tight letter-spacing and semi-bold weights to create a strong visual anchor. Body text remains airy and functional. Labels are always uppercase when using the monospaced font to create a distinct "cataloging" feel.

## Layout & Spacing

The layout utilizes a **fluid-to-fixed hybrid model**. The main photo feed uses a masonry grid that stretches to fill the viewport width, while utility pages (settings, profile) conform to a 12-column fixed grid with a 1440px max-width.

- **Masonry Logic:** Photos maintain their aspect ratio. On desktop, the grid should default to 4 or 5 columns depending on viewport width. On mobile, this reflows to 2 columns.
- **AI Search Bar:** Positioned centrally at the top with significant padding, expanding into a full-screen blurred overlay when active.
- **Rhythm:** Spacing follows a strict 8px linear scale. Large-scale photography sections should use the 48px margin to feel like a modern physical gallery.

## Elevation & Depth

Visual hierarchy is achieved through **low-contrast outlines** and **tonal layering** rather than traditional shadows.

- **Default State:** Elements exist on a flat plane. Cards and containers are defined by 1px borders (#E5E7EB) or subtle background shifts.
- **Interaction Depth:** On hover, images should scale slightly (1.02x) and receive a high-diffused, low-opacity shadow (0px 20px 40px rgba(0,0,0,0.05)) to suggest "lifting" off the page.
- **AI Layers:** The AI search interface and modals use a high-strength backdrop blur (20px) with a 90% white tint, creating a "Glassmorphism" effect that keeps the user's photos visible but out of focus.

## Shapes

The shape language is **Soft** and architectural. 
- **Images:** Use a 0.5rem (rounded-lg) corner radius to soften the edges of photography without making them feel informal.
- **Input Fields:** Use 0.25rem (base) radius for a precise, technical look.
- **AI Tokens/Chips:** Use full pill-shapes (rounded-full) to differentiate them from static metadata labels.
- **Buttons:** Primary buttons use a 0.25rem radius to maintain the professional, "tool-like" aesthetic.

## Components

### Buttons
- **Primary:** Solid black background, white text. No border. High-contrast.
- **AI Action:** Solid indigo (#4F46E5) with white text, used only for "Generate," "Magic Search," or "Curate."
- **Ghost:** Transparent background with 1px black border. Used for secondary actions like "Edit Metadata."

### AI Search Interface
The search bar is the hero component. It should feature a "shimmer" border effect using the indigo accent color when active. Placeholder text should cycle through semantic examples (e.g., "Find photos of my dog at the beach at sunset").

### Image Cards
Images are borderless. On hover, a gradient overlay (bottom-to-top, black-to-transparent) appears to reveal white metadata text and quick-action icons (favorite, add to album).

### Chips & Tags
- **System Tags:** (e.g., File Type, Date) JetBrains Mono, light gray background, no border.
- **AI Tags:** (e.g., "Golden Hour", "Mountain") Indigo text on a faint indigo tint background (#EEF2FF).

### Input Fields
Minimalist 1px bottom-border only for a "form" feel, or 1px all-around border for search. Focus state uses a black 2px border—no glow.