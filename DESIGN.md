---
version: alpha
name: Fintech Design System Template
description: A sophisticated, serif-forward fintech interface focusing on institutional clarity and editorial density.
colors:
  background: "#F6F5ED"
  primary: "#2C2C2A"
  secondary: "#EBEAE4"
  accent: "#8BB8A8"
  success: "#B9FBC0"
  text-main: "#444444"
  text-heading: "#2C2C2A"
  border: "#D5D4CD"
typography:
  fontFamily: "'Inter', sans-serif"
  serifFamily: "serif"
  monoFamily: "monospace"
  h1: "text-4xl to 6xl, font-serif, tracking-tight"
  body: "text-base, font-medium, leading-relaxed"
  caption: "text-xs, font-mono"
spacing:
  base: "4px"
  container: "max-w-5xl"
rounded:
  default: "6px"
  pill: "9999px"
  card: "12px"
components:
  button-primary: "bg-[#2C2C2A], text-white"
  button-ghost: "border-[#D5D4CD], hover:bg-white"
  badge: "bg-[#B9FBC0], text-[#0B4019], font-mono"
---

## Overview
The Fintech Design System (DayTrade) features a refined, high-density visual personality that balances classical typography with modern utility. The palette is dominated by warm, paper-like neutrals and deep charcoal tones, moving away from typical blue-centric financial tech. The layout is spacious yet information-dense, utilizing a rigid grid and thin borders to create a structured, professional feel. Motion is minimal and purely functional, focusing on state transitions.

## Colors
- **Base Neutrals**: Primary background `#F6F5ED` (Paper), secondary background/pill fill `#EBEAE4`, and primary border color `#D5D4CD`.
- **Typography & UI Tones**: Main heading and primary buttons use `#2C2C2A` (Charcoal). Subheadings and secondary text use `#4A4A4A` and `#555`.
- **Status Colors**: Success states utilize a bright mint `#B9FBC0` with deep green text `#0B4019`. Negative states utilize `red-200` with `red-900` text.
- **Secondary Accents**: Soft muted teal `#8BB8A8` for categorical UI elements.

## Typography
- **Serif Interface**: Used for brand marks and primary headings (h1). Features mixed regular and italic weights for an editorial look. `font-serif` with `tracking-tight`.
- **Functional Sans**: Inter is used for all body text and UI controls. `font-medium` is the default weight for readability against the off-white background.
- **System Mono**: Used for metadata, color codes, table headers, and badges. Specifically used in uppercase with `tracking-widest` for section headers.

## Layout
- **Max Width**: The layout is contained within a `max-w-5xl` central column.
- **Grid System**: A two-column grid on desktop (`md:grid-cols-2`) with large gutters (`gap-x-16`).
- **Density**: High vertical spacing between major sections (`mb-12`, `gap-y-12`) contrasted with compact component padding.

## Elevation & Depth
- **Low Elevation**: Most components use a very subtle `shadow-sm` for slight separation from the paper background.
- **High Elevation**: Search/Omnibar components use a pronounced shadow: `shadow-[0_20px_40px_-10px_rgba(0,0,0,0.4)]` to indicate focus and priority.
- **Borders**: Thin, defined borders (`1px solid #D5D4CD`) are the primary method for defining hierarchy rather than heavy shadows.

## Shapes
- **Corner Radius**: Standard components like buttons and table containers use a `6px` (`rounded-md`) radius.
- **Interactive Pills**: Brand elements and badges use a full `rounded-full` pill shape.
- **Search Surfaces**: Large input bars use a `12px` (`rounded-xl`) radius.

## Components
- **Navigation Bar**: A utility-first header with a centered or left-aligned pill brand mark and right-aligned ghost buttons and search icons.
- **Buttons**: Square-edged with a `6px` radius. Hover states shift from transparent/border to white backgrounds or charcoal to black.
- **Input Groups**: Dark-themed omnibars (`bg-[#4A4A4A]`) with integrated buttons and icon prefixes.
- **Data Tables**: Bordered containers with monospace headers and transition-enabled row hover states.
- **Status Badges**: Small, monospace, semi-bold pill or soft-square labels.

## Page Sections
### Global Header
- **Composition**: Flex-row spanning full width.
- **Brand Mark**: A pill-shaped container with a serif wordmark: "Day" (Regular) "Trade" (Italic).
- **Actions**: Utility buttons for location (icon + text), navigation (text), and a dedicated square search button.

### Document Header
- **Hierarchy**: Large H1 "Design System" followed by a medium-weight lead paragraph.
- **Style**: Serif typography for titles to establish an institutional tone.

### Design Asset Showcase
- **Color Grid**: 2x3 or 3x3 layout of swatches with hex codes in monospace text below.
- **Typography Stack**: Visual examples of headings and body copy with labels.
- **Component Gallery**: Flex-wrapped buttons and input examples showcasing varied widths and interactions.

### Data Grid (Table)
- **Structure**: Clean, border-collapsed table inside a rounded container.
- **Header**: Monospace, uppercase text with a distinct background fill (`#EBEAE4`).
- **Rows**: Dynamic data rows with hover effects and right-aligned numeric data.

## Motion & Interaction
- **Hover States**: Subtle color transitions (`transition-colors`) on all interactive buttons and table rows.
- **Selection**: Custom selection color using `selection:bg-[#EBEAE4]` to match the secondary background tone.
- **Interactive Pausing**: Evidence of a global performance controller that can toggle CSS animations and transitions across the document via `data-aura-preview-paused` attribute.

## Do's and Don'ts
- **Do**: Use high-contrast serif italics for emphasis within headings.
- **Do**: Maintain the off-white paper background (`#F6F5ED`) for all main surfaces.
- **Don't**: Use vibrant primary colors (like bright blues or purples) which would clash with the muted, sophisticated palette.
- **Don't**: Overuse shadows; rely on borders for structural definition.

## Accessibility
- **Contrast**: High-contrast charcoal text on cream background meets standards for readability.
- **Semantic HTML**: Proper use of `header`, `main`, `section`, and `table` tags.
- **Focus States**: Explicit `focus:outline-none` on search inputs, requiring alternative visual focus indicators for full compliance.

## Assets
- **Tailwind CSS**: https://cdn.tailwindcss.com
- **Iconify Framework**: https://code.iconify.design/iconify-icon/1.0.7/iconify-icon.min.js
- **Google Fonts API**: https://fonts.googleapis.com
- **Google Fonts Static**: https://fonts.gstatic.com
- **Inter Font**: https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&amp;display=swap
