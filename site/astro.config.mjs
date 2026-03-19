// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
	integrations: [
		starlight({
			title: 'Chimera',
			tagline: 'A composable coding agent framework',
			description: 'Synthesize codebases from specifications. Compose any coding agent architecture.',
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/0bserver07/chimera' },
			],
			logo: {
				dark: './src/assets/logo-dark.svg',
				light: './src/assets/logo-light.svg',
				replacesTitle: false,
			},
			customCss: ['./src/styles/custom.css'],
			defaultLocale: 'root',
			// Force dark mode as default (Mintlify-style)
			head: [
				{
					tag: 'script',
					content: `
						// Default to dark theme
						if (!localStorage.getItem('starlight-theme')) {
							localStorage.setItem('starlight-theme', 'dark');
						}
					`,
				},
			],
			sidebar: [
				{
					label: 'Getting Started',
					items: [
						{ label: 'Introduction', slug: 'getting-started' },
						{ label: 'Quickstart', slug: 'quickstart' },
						{ label: 'Architecture', slug: 'architecture' },
					],
				},
				{
					label: 'Concepts',
					autogenerate: { directory: 'concepts' },
				},
				{
					label: 'Guides',
					autogenerate: { directory: 'guides' },
				},
				{
					label: 'Modules',
					collapsed: true,
					autogenerate: { directory: 'modules' },
				},
				{
					label: 'Workflows',
					autogenerate: { directory: 'workflows' },
				},
				{
					label: 'API Reference',
					collapsed: true,
					autogenerate: { directory: 'reference' },
				},
			],
		}),
	],
});
