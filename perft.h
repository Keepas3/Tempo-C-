#pragma once
#include <cstdint>
#include "board.h"
#include "movegen.h"
#include "makemove.h"

inline uint64_t perft(Board& board, int depth) {
    if (depth == 0) return 1;

    MoveList list;
    generate_legal_moves(board, list);

    uint64_t nodes = 0;
    for (int i = 0; i < list.count; ++i) {
        Move move = list.moves[i];
        UndoInfo undo = make_move(board, move);
        nodes += perft(board, depth - 1);
        unmake_move(board, move, undo);
    }

    return nodes;
}
