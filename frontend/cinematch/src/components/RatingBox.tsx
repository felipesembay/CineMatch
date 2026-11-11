import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Star } from 'lucide-react';
import { api } from '../lib/api';
import { track } from '../lib/events';

/** Nota de 1 a 5 com comentário opcional. Alimenta "Minha lista" e a recomendação. */
export function RatingBox({ itemId }: { itemId: number }) {
  const qc = useQueryClient();
  const ratings = useQuery({ queryKey: ['ratings'], queryFn: api.ratings });
  const current = ratings.data?.find(r => r.item.id === itemId);
  const [value, setValue] = useState(0);
  const [comment, setComment] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setValue(current?.value ?? 0);
    setComment(current?.comment ?? '');
  }, [current?.value, current?.comment]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['ratings'] });
    qc.invalidateQueries({ queryKey: ['recommendations'] });
  };
  const save = useMutation({
    mutationFn: () => api.rate(itemId, value, comment.trim() || null),
    onSuccess: () => {
      invalidate();
      setSaved(true);
      track('rating', 'detail', { item_id: itemId });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteRating(itemId),
    onSuccess: () => {
      invalidate();
      setValue(0);
      setComment('');
    },
  });

  return (
    <div>
      <div className="flex items-center gap-1" role="radiogroup" aria-label="Sua nota">
        {[1, 2, 3, 4, 5].map(v => (
          <button
            key={v}
            role="radio"
            aria-checked={value === v}
            aria-label={`${v} estrela${v > 1 ? 's' : ''}`}
            onClick={() => {
              setValue(v);
              setSaved(false);
            }}
            className="rounded p-1 text-amber hover:scale-110"
          >
            <Star size={26} fill={v <= value ? 'currentColor' : 'none'} />
          </button>
        ))}
      </div>
      <label className="mt-4 block text-sm text-muted">
        Comentário (opcional)
        <textarea
          value={comment}
          maxLength={1000}
          rows={3}
          onChange={e => {
            setComment(e.target.value);
            setSaved(false);
          }}
          placeholder="O que você achou?"
          className="field mt-2 resize-y"
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          className="btn btn-primary px-4 py-2 text-sm"
          disabled={!value || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Salvando...' : current ? 'Atualizar avaliação' : 'Salvar avaliação'}
        </button>
        {current && (
          <button
            className="text-sm text-muted hover:text-ink"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Remover
          </button>
        )}
        {saved && !save.isPending && <span className="text-xs text-amber">Salvo na sua lista.</span>}
        {(save.isError || remove.isError) && (
          <span className="text-xs text-red-300">Não foi possível salvar. Tente novamente.</span>
        )}
      </div>
    </div>
  );
}
