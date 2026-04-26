// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
	site: 'https://0bserver07.github.io',
	base: '/chimera',
	integrations: [
		starlight({
			title: 'Chimera',
			tagline: 'Composable coding agents in Python',
			description: 'Build, compose, and deploy coding agents from modular primitives.',
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
				{
					tag: 'script',
					attrs: { src: '/mermaid-init.js', type: 'module', defer: true },
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
				{
					label: 'Otter',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'otter/quickstart' },
								{ label: 'Providers', slug: 'otter/providers' },
								{ label: 'Models', slug: 'otter/models' },
								{ label: 'Sessions', slug: 'otter/sessions' },
								{ label: 'Share', slug: 'otter/share' },
								{ label: 'Server', slug: 'otter/server' },
							],
						},
						{
							label: 'Per-Area',
							items: [
								{ label: 'Parity Matrix', slug: 'otter/parity-matrix' },
								{ label: 'Agents', slug: 'otter/agents' },
								{ label: 'Commands', slug: 'otter/commands' },
								{ label: 'Rules', slug: 'otter/rules' },
								{ label: 'Slash Commands', slug: 'otter/slash-commands' },
								{ label: 'MCP', slug: 'otter/mcp' },
								{ label: 'LSP', slug: 'otter/lsp' },
								{ label: 'ACP', slug: 'otter/acp' },
								{ label: 'Plugins', slug: 'otter/plugins' },
							],
						},
						{
							label: 'Policy',
							items: [
								{ label: 'Security and Trademarks', slug: 'otter/security-and-trademarks' },
								{ label: 'Trademark Policy', slug: 'otter/trademark-policy' },
							],
						},
					],
				},
			],
		}),
	],
});
