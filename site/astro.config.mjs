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
					label: 'Mink',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'mink/quickstart' },
								{ label: 'Providers', slug: 'mink/providers' },
								{ label: 'Models', slug: 'mink/models' },
								{ label: 'Sessions', slug: 'mink/sessions' },
								{ label: 'Runs', slug: 'mink/runs' },
								{ label: 'Settings', slug: 'mink/settings' },
							],
						},
						{
							label: 'Per-Area',
							items: [
								{ label: 'Parity Matrix', slug: 'mink/parity-matrix' },
								{ label: 'Slash Commands', slug: 'mink/slash-commands' },
								{ label: 'Tools', slug: 'mink/tools' },
								{ label: 'Permissions', slug: 'mink/permissions' },
								{ label: 'Output Formats', slug: 'mink/output-formats' },
								{ label: 'MCP Advanced', slug: 'mink/mcp-advanced' },
								{ label: 'Subagents', slug: 'mink/subagents' },
								{ label: 'Agent Teams', slug: 'mink/agent-teams' },
								{ label: 'Memory', slug: 'mink/memory' },
								{ label: 'Benchmarks', slug: 'mink/benchmarks' },
								{ label: 'Remote', slug: 'mink/remote' },
							],
						},
						{
							label: 'Policy',
							items: [
								{ label: 'Security and Licenses', slug: 'mink/security-and-licenses' },
							],
						},
					],
				},
				{
					label: 'Otter',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'otter/quickstart' },
								{ label: 'CLI Reference', slug: 'otter/cli-reference' },
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
				{
					label: 'Ferret',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'ferret/quickstart' },
								{ label: 'Providers', slug: 'ferret/providers' },
							],
						},
						{
							label: 'Per-Area',
							items: [
								{ label: 'Parity Matrix', slug: 'ferret/parity-matrix' },
								{ label: 'Sandbox', slug: 'ferret/sandbox' },
								{ label: 'Approval', slug: 'ferret/approval' },
								{ label: 'IDE Bridge', slug: 'ferret/ide' },
								{ label: 'Cloud Bridge', slug: 'ferret/cloud-bridge' },
							],
						},
						{
							label: 'Policy',
							items: [
								{ label: 'Security and Trademarks', slug: 'ferret/security-and-trademarks' },
							],
						},
					],
				},
				{
					label: 'Weasel',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'weasel/quickstart' },
								{ label: 'Providers', slug: 'weasel/providers' },
								{ label: 'Modes', slug: 'weasel/modes' },
							],
						},
						{
							label: 'Per-Area',
							items: [
								{ label: 'SDK', slug: 'weasel/sdk' },
								{ label: 'Extensions', slug: 'weasel/extensions' },
							],
						},
					],
				},
				{
					label: 'Shrew',
					collapsed: true,
					items: [
						{
							label: 'Core',
							items: [
								{ label: 'Quickstart', slug: 'shrew/quickstart' },
								{ label: 'Small-Model Setup', slug: 'shrew/small-model-setup' },
							],
						},
						{
							label: 'Per-Area',
							items: [
								{ label: 'Parity Matrix', slug: 'shrew/parity-matrix' },
								{ label: 'Skills', slug: 'shrew/skills' },
								{ label: 'Extensions', slug: 'shrew/extensions' },
								{ label: 'Benchmarks', slug: 'shrew/benchmarks' },
							],
						},
						{
							label: 'Policy',
							items: [
								{ label: 'Security and Trademarks', slug: 'shrew/security-and-trademarks' },
							],
						},
					],
				},
				{
					label: 'Releases',
					collapsed: true,
					items: [
						{ label: '0.5.0 — Five-Strong Family', slug: 'releases/0.5.0' },
					],
				},
			],
		}),
	],
});
