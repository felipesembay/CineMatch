// Rótulos pt-BR para valores vindos do TMDB (a API mantém os nomes originais, usados nos filtros).
const GENRES: Record<string, string> = {
  Action: 'Ação',
  'Action & Adventure': 'Ação e aventura',
  Adventure: 'Aventura',
  Animation: 'Animação',
  Comedy: 'Comédia',
  Crime: 'Crime',
  Documentary: 'Documentário',
  Drama: 'Drama',
  Family: 'Família',
  Fantasy: 'Fantasia',
  History: 'História',
  Horror: 'Terror',
  Kids: 'Infantil',
  Music: 'Música',
  Mystery: 'Mistério',
  News: 'Notícias',
  Reality: 'Reality',
  Romance: 'Romance',
  'Sci-Fi & Fantasy': 'Ficção científica e fantasia',
  'Science Fiction': 'Ficção científica',
  Soap: 'Novela',
  Talk: 'Talk show',
  Thriller: 'Suspense',
  'TV Movie': 'Filme para TV',
  War: 'Guerra',
  'War & Politics': 'Guerra e política',
  Western: 'Faroeste',
};

const STATUS: Record<string, string> = {
  Released: 'Lançado',
  'Post Production': 'Pós-produção',
  'In Production': 'Em produção',
  Planned: 'Planejado',
  Rumored: 'Rumor',
  Canceled: 'Cancelado',
  'Returning Series': 'Em exibição',
  Ended: 'Encerrada',
  Pilot: 'Piloto',
};

export const genreLabel = (name: string): string => GENRES[name] ?? name;
export const statusLabel = (status: string | null): string => (status ? STATUS[status] ?? status : '');

// O backend gera `reason` em pt-BR com nomes de gênero do TMDB ("Também Action e Crime").
export const reasonLabel = (reason: string | null | undefined): string | null | undefined =>
  reason?.replace(/\b(Também|em|e) ([A-Z][\w&\- ]*?)(?= e |$)/g, (_, p, g) => `${p} ${genreLabel(g)}`);
